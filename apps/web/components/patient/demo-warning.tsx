export function DemoWarning() {
  return (
    <aside
      aria-label="Synthetic demonstration warning"
      role="note"
      style={{
        background: "#fff4e5",
        border: "1px solid #d97706",
        borderRadius: "0.75rem",
        color: "#5b2c06",
        marginBlock: "1rem",
        padding: "0.875rem 1rem",
      }}
    >
      <strong>Synthetic demonstration only.</strong> Do not enter real health information, names,
      email addresses, phone numbers, or medical-record numbers.
    </aside>
  );
}
