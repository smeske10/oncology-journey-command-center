"""Immutable navigator authorization for pre-creation transportation evidence."""

from alembic import op

revision = "0008_need_creation_approval"
down_revision = "0007_database_least_privilege"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(SCHEMA_SQL)
    op.execute(FUNCTION_SQL)
    for table in ("need_creation_policy", "need_creation_proposal", "need_creation_decision"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation();
            REVOKE ALL ON {table} FROM PUBLIC, ojcc_app;
            GRANT SELECT ON {table} TO ojcc_app;
        """)
    op.execute("""
        GRANT INSERT ON need_creation_proposal, need_creation_decision TO ojcc_app;
        CREATE TRIGGER trg_need_creation_proposal BEFORE INSERT ON need_creation_proposal
        FOR EACH ROW EXECUTE FUNCTION guard_need_creation_proposal();
        CREATE TRIGGER trg_need_creation_decision BEFORE INSERT ON need_creation_decision
        FOR EACH ROW EXECUTE FUNCTION guard_need_creation_decision();
        CREATE TRIGGER trg_need_creation_correction BEFORE INSERT ON check_in_submission
        FOR EACH ROW EXECUTE FUNCTION guard_need_creation_correction();
        CREATE TRIGGER trg_need_creation_authority BEFORE UPDATE OR DELETE ON role_assignment
        FOR EACH ROW EXECUTE FUNCTION guard_need_creation_authority();
        CREATE TRIGGER trg_need_creation_authority_write
        BEFORE INSERT OR UPDATE OR DELETE ON role_assignment
        FOR EACH STATEMENT EXECUTE FUNCTION lock_need_creation_authority();
    """)
    for signature in (
        "need_creation_chain(uuid, uuid)",
        "need_creation_evidence(uuid, uuid, uuid)",
        "need_creation_authority(uuid, uuid)",
        "guard_need_creation_proposal()",
        "guard_need_creation_decision()",
        "guard_need_creation_correction()",
        "guard_need_creation_authority()",
        "lock_need_creation_authority()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, ojcc_app")


def downgrade() -> None:
    # No silent removal of immutable authorization history.
    raise RuntimeError("Need creation authorization history cannot be downgraded")


SCHEMA_SQL = """
CREATE TABLE need_creation_policy (
    policy_id text NOT NULL, version integer NOT NULL,
    required_approver_role text NOT NULL CHECK (required_approver_role = 'navigator'),
    required_approval_count integer NOT NULL CHECK (required_approval_count = 1),
    allow_self_approval boolean NOT NULL CHECK (allow_self_approval),
    PRIMARY KEY (policy_id, version)
);
INSERT INTO need_creation_policy VALUES ('transportation-reported-need', 1, 'navigator', 1, true);

CREATE TABLE need_creation_proposal (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organization(id),
    chain_root_id uuid NOT NULL, source_submission_id uuid NOT NULL,
    patient_id uuid NOT NULL, care_episode_id uuid NOT NULL,
    proposed_by_user_id uuid NOT NULL REFERENCES user_account(id),
    proposer_role_assignment_id uuid NOT NULL,
    rationale text NOT NULL CHECK (length(btrim(rationale)) BETWEEN 1 AND 4000),
    evidence jsonb NOT NULL, canonical_evidence text NOT NULL,
    evidence_sha256 text NOT NULL CHECK (length(evidence_sha256)=64),
    policy_id text NOT NULL, policy_version integer NOT NULL, policy_snapshot jsonb NOT NULL,
    proposed_at timestamptz NOT NULL,
    UNIQUE (organization_id, id),
    FOREIGN KEY (policy_id, policy_version) REFERENCES need_creation_policy(policy_id, version),
    FOREIGN KEY (organization_id, patient_id, care_episode_id, source_submission_id)
        REFERENCES check_in_submission(organization_id, patient_id, care_episode_id, id),
    FOREIGN KEY (organization_id, patient_id, care_episode_id, chain_root_id)
        REFERENCES check_in_submission(organization_id, patient_id, care_episode_id, id),
    FOREIGN KEY (organization_id, proposed_by_user_id, proposer_role_assignment_id)
        REFERENCES role_assignment(organization_id, user_id, id)
);
CREATE INDEX ix_need_creation_proposal_org_chain ON need_creation_proposal
    (organization_id, chain_root_id);

CREATE TABLE need_creation_decision (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organization(id),
    proposal_id uuid NOT NULL,
    authorized_by_user_id uuid NOT NULL REFERENCES user_account(id),
    qualifying_role_assignment_id uuid NOT NULL,
    qualifying_role_snapshot text NOT NULL CHECK (qualifying_role_snapshot='navigator'),
    decision text NOT NULL CHECK (decision IN ('approved', 'declined')),
    reason text CHECK (length(reason)<=4000),
    outcome text NOT NULL CHECK (outcome IN
        ('created','declined','stale_evidence','already_created','proposal_decided')),
    chain_root_id uuid NOT NULL,
    reported_need_id uuid,
    authorized_at timestamptz NOT NULL,
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, proposal_id)
        REFERENCES need_creation_proposal(organization_id, id),
    FOREIGN KEY (organization_id, chain_root_id)
        REFERENCES check_in_submission(organization_id, id),
    FOREIGN KEY (organization_id, reported_need_id) REFERENCES reported_need(organization_id, id),
    FOREIGN KEY (organization_id, authorized_by_user_id, qualifying_role_assignment_id)
        REFERENCES role_assignment(organization_id, user_id, id),
    CHECK ((outcome='created') = (reported_need_id IS NOT NULL)),
    CHECK (decision <> 'declined' OR coalesce(length(btrim(reason)),0) > 0)
);
CREATE UNIQUE INDEX ix_need_creation_decision_org_chain_created
    ON need_creation_decision(organization_id, chain_root_id) WHERE outcome='created';
CREATE INDEX ix_need_creation_decision_org_proposal
    ON need_creation_decision(organization_id, proposal_id);
"""

FUNCTION_SQL = """
CREATE FUNCTION lock_need_creation_authority() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    -- Statement-level ordering acquires this lock before any assignment row lock.
    PERFORM pg_advisory_xact_lock(hashtextextended('need-creation-authority',0));
    RETURN NULL;
END $$;

CREATE FUNCTION need_creation_chain(p_org uuid, p_leaf uuid) RETURNS uuid[]
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    cursor_id uuid := p_leaf; ids uuid[] := '{}'; row public.check_in_submission;
    patient uuid; episode uuid;
BEGIN
    LOOP
        IF cursor_id = ANY(ids) THEN
            RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514';
        END IF;
        SELECT * INTO row FROM public.check_in_submission
            WHERE organization_id=p_org AND id=cursor_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514'; END IF;
        IF patient IS NULL THEN patient := row.patient_id; episode := row.care_episode_id; END IF;
        IF row.patient_id <> patient OR row.care_episode_id <> episode THEN
            RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514';
        END IF;
        ids := array_append(ids, cursor_id);
        EXIT WHEN row.supersedes_submission_id IS NULL;
        cursor_id := row.supersedes_submission_id;
    END LOOP;
    RETURN ids;
END $$;

CREATE FUNCTION need_creation_authority(p_org uuid, p_user uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE ids uuid[];
BEGIN
    -- Shared command lock also protects the absence of overlapping assignments.
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('need-creation-authority',0));
    PERFORM 1 FROM public.user_account WHERE id=p_user AND is_active FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'navigator_authority_invalid' USING ERRCODE='42501'; END IF;
    -- Hold qualifying authority against concurrent revocation until commit.
    PERFORM 1 FROM public.role_assignment WHERE organization_id=p_org AND user_id=p_user
        AND role='navigator' FOR SHARE;
    SELECT array_agg(id) INTO ids FROM public.role_assignment
        WHERE organization_id=p_org AND user_id=p_user AND role='navigator'
        AND granted_at<=clock_timestamp()
        AND (revoked_at IS NULL OR revoked_at>clock_timestamp());
    IF coalesce(cardinality(ids),0) <> 1 THEN
        RAISE EXCEPTION 'navigator_authority_invalid' USING ERRCODE='42501';
    END IF;
    RETURN ids[1]; -- cardinality checked, never resolve ambiguity by position
END $$;

CREATE FUNCTION need_creation_evidence(p_org uuid, p_root uuid, p_leaf uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    ids uuid[]; submission public.check_in_submission; definition public.check_in_definition;
    questions jsonb; answers jsonb; question jsonb; answer jsonb;
BEGIN
    ids := public.need_creation_chain(p_org, p_leaf);
    IF ids[cardinality(ids)] <> p_root OR EXISTS (
        SELECT 1 FROM public.check_in_submission WHERE supersedes_submission_id=p_leaf
    ) THEN RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT submission FROM public.check_in_submission
        WHERE organization_id=p_org AND id=p_leaf;
    SELECT * INTO STRICT definition FROM public.check_in_definition
        WHERE organization_id=p_org AND id=submission.check_in_definition_id FOR SHARE;
    IF definition.slug <> 'weekly-synthetic-check-in' OR definition.version<>2
        OR definition.questionnaire->>'version' IS DISTINCT FROM 'weekly-synthetic-check-in-v2'
        OR submission.answers->>'questionnaire_version'
            IS DISTINCT FROM 'weekly-synthetic-check-in-v2'
        OR jsonb_typeof(definition.questionnaire->'questions') IS DISTINCT FROM 'array'
        OR jsonb_typeof(submission.answers->'items') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514';
    END IF;
    SELECT jsonb_agg(item) INTO questions
        FROM jsonb_array_elements(definition.questionnaire->'questions') item
        WHERE item->>'link_id'='transportation';
    SELECT jsonb_agg(item) INTO answers FROM jsonb_array_elements(submission.answers->'items') item
        WHERE item->>'link_id'='transportation';
    IF coalesce(jsonb_array_length(questions),0)<>1
        OR coalesce(jsonb_array_length(answers),0)<>1 THEN
        RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514';
    END IF;
    question := questions->0; answer := answers->0;
    IF question->>'label' IS DISTINCT FROM 'Need synthetic transportation support?'
        OR question->'options' IS DISTINCT FROM
            '[{"value":"yes","label":"Yes"},{"value":"no","label":"No"}]'::jsonb
        OR answer->'value' IS DISTINCT FROM '"yes"'::jsonb THEN
        RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.reported_need
        WHERE organization_id=p_org AND source_submission_id=ANY(ids)) THEN
        RAISE EXCEPTION 'already_created' USING ERRCODE='23514';
    END IF;
    RETURN jsonb_build_object(
        'schema_version', 1, 'canonicalization', 'postgresql-jsonb-text-v1',
        'organization_id', p_org, 'patient_id', submission.patient_id,
        'care_episode_id', submission.care_episode_id,
        'chain_root_id', p_root, 'source_submission_id', p_leaf,
        'check_in_definition_id', definition.id, 'definition_version', definition.version,
        'questionnaire_version', definition.questionnaire->>'version',
        'question', question, 'answer', answer, 'display_text', 'yes',
        'kind', 'transportation', 'initial_state', 'open');
END $$;

CREATE FUNCTION guard_need_creation_correction() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE ids uuid[]; parent public.check_in_submission;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'read_committed_required' USING ERRCODE='23514';
    END IF;
    IF NEW.supersedes_submission_id IS NULL THEN RETURN NEW; END IF;
    ids := public.need_creation_chain(NEW.organization_id, NEW.supersedes_submission_id);
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'need-creation:' || NEW.organization_id::text || ':' || ids[cardinality(ids)]::text, 0));
    SELECT * INTO STRICT parent FROM public.check_in_submission
        WHERE organization_id=NEW.organization_id AND id=NEW.supersedes_submission_id;
    IF parent.patient_id<>NEW.patient_id OR parent.care_episode_id<>NEW.care_episode_id
        OR EXISTS (SELECT 1 FROM public.check_in_submission
            WHERE supersedes_submission_id=NEW.supersedes_submission_id) THEN
        RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION guard_need_creation_proposal() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE policy public.need_creation_policy; existing public.need_creation_proposal;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'read_committed_required' USING ERRCODE='23514';
    END IF;
    NEW.proposer_role_assignment_id := public.need_creation_authority(
        NEW.organization_id, NEW.proposed_by_user_id);
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'need-creation:' || NEW.organization_id::text || ':' || NEW.chain_root_id::text, 0));
    NEW.proposer_role_assignment_id := public.need_creation_authority(
        NEW.organization_id, NEW.proposed_by_user_id);
    SELECT * INTO existing FROM public.need_creation_proposal WHERE id=NEW.id;
    IF FOUND THEN
        IF (existing.organization_id, existing.chain_root_id, existing.source_submission_id,
            existing.proposed_by_user_id, existing.rationale) IS DISTINCT FROM
           (NEW.organization_id, NEW.chain_root_id, NEW.source_submission_id,
            NEW.proposed_by_user_id, NEW.rationale) THEN
            RAISE EXCEPTION 'request_conflict' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    NEW.evidence := public.need_creation_evidence(
        NEW.organization_id, NEW.chain_root_id, NEW.source_submission_id);
    NEW.patient_id := (NEW.evidence->>'patient_id')::uuid;
    NEW.care_episode_id := (NEW.evidence->>'care_episode_id')::uuid;
    NEW.canonical_evidence := NEW.evidence::text;
    NEW.evidence_sha256 := encode(sha256(convert_to(NEW.canonical_evidence,'UTF8')), 'hex');
    SELECT * INTO STRICT policy FROM public.need_creation_policy
        WHERE policy_id='transportation-reported-need' AND version=1;
    NEW.policy_id := policy.policy_id; NEW.policy_version := policy.version;
    NEW.policy_snapshot := to_jsonb(policy);
    NEW.proposed_at := clock_timestamp();
    INSERT INTO public.audit_event
        (id,organization_id,actor_type,actor_user_id,entity_type,entity_id,event_type,payload)
        VALUES (gen_random_uuid(),NEW.organization_id,'user',NEW.proposed_by_user_id,
            'need_creation_proposal',NEW.id,'need_creation_proposed',
            jsonb_build_object('evidence_sha256',NEW.evidence_sha256));
    RETURN NEW;
END $$;

CREATE FUNCTION guard_need_creation_decision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE proposal public.need_creation_proposal; existing public.need_creation_decision;
    current_evidence jsonb;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'read_committed_required' USING ERRCODE='23514';
    END IF;
    NEW.qualifying_role_assignment_id := public.need_creation_authority(
        NEW.organization_id, NEW.authorized_by_user_id);
    SELECT * INTO proposal FROM public.need_creation_proposal
        WHERE organization_id=NEW.organization_id AND id=NEW.proposal_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'proposal_not_found' USING ERRCODE='23514'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'need-creation:' || NEW.organization_id::text || ':' || proposal.chain_root_id::text, 0));
    NEW.qualifying_role_assignment_id := public.need_creation_authority(
        NEW.organization_id, NEW.authorized_by_user_id);
    SELECT * INTO existing FROM public.need_creation_decision WHERE id=NEW.id;
    IF FOUND THEN
        IF (existing.organization_id,existing.proposal_id,existing.authorized_by_user_id,
            existing.decision,existing.reason) IS DISTINCT FROM
           (NEW.organization_id,NEW.proposal_id,NEW.authorized_by_user_id,
            NEW.decision,NEW.reason) THEN
            RAISE EXCEPTION 'request_conflict' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    NEW.chain_root_id := proposal.chain_root_id;
    NEW.qualifying_role_snapshot := 'navigator';
    NEW.authorized_at := clock_timestamp();
    NEW.reported_need_id := NULL;
    IF EXISTS (SELECT 1 FROM public.need_creation_decision WHERE proposal_id=proposal.id
        AND outcome IN ('created','declined')) THEN
        NEW.outcome := 'proposal_decided';
    ELSIF NEW.decision='declined' THEN
        NEW.outcome := 'declined';
    ELSE
        BEGIN
            current_evidence := public.need_creation_evidence(
                NEW.organization_id,proposal.chain_root_id,proposal.source_submission_id);
            IF current_evidence IS DISTINCT FROM proposal.evidence THEN
                RAISE EXCEPTION 'stale_evidence' USING ERRCODE='23514';
            END IF;
            NEW.reported_need_id := gen_random_uuid();
            INSERT INTO public.reported_need
                (id,organization_id,patient_id,care_episode_id,source_submission_id,kind,status,evidence)
                VALUES (NEW.reported_need_id, NEW.organization_id, proposal.patient_id,
                    proposal.care_episode_id,proposal.source_submission_id,'transportation','open',
                    jsonb_build_array(jsonb_build_object('source_submission_id',
                        proposal.source_submission_id,'field','transportation','text','yes',
                        'value','yes')));
            NEW.outcome := 'created';
        EXCEPTION WHEN check_violation THEN
            IF SQLERRM NOT IN ('stale_evidence','already_created') THEN RAISE; END IF;
            NEW.outcome := SQLERRM;
            NEW.reported_need_id := NULL;
        END;
    END IF;
    INSERT INTO public.audit_event
        (id,organization_id,actor_type,actor_user_id,entity_type,entity_id,event_type,payload)
        VALUES (gen_random_uuid(),NEW.organization_id,'user',NEW.authorized_by_user_id,
            'need_creation_decision',NEW.id,'need_creation_' || NEW.outcome,
            jsonb_build_object('proposal_id',proposal.id,'reported_need_id',NEW.reported_need_id,
                'evidence_sha256',proposal.evidence_sha256,'outcome',NEW.outcome));
    RETURN NEW;
END $$;

CREATE FUNCTION guard_need_creation_authority() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE last_used timestamptz;
BEGIN
    SELECT max(used_at) INTO last_used FROM (
        SELECT proposed_at AS used_at FROM public.need_creation_proposal
            WHERE proposer_role_assignment_id=OLD.id
        UNION ALL SELECT authorized_at FROM public.need_creation_decision
            WHERE qualifying_role_assignment_id=OLD.id
    ) history;
    IF last_used IS NOT NULL AND (TG_OP='DELETE' OR
        (NEW.organization_id,NEW.user_id,NEW.role,NEW.granted_at) IS DISTINCT FROM
        (OLD.organization_id,OLD.user_id,OLD.role,OLD.granted_at) OR
        (NEW.revoked_at IS NOT NULL AND NEW.revoked_at<=last_used)) THEN
        RAISE EXCEPTION 'need_creation_authority_history_immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
"""
