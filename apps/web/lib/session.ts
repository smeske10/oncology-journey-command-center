export const demoRoles = ["administrator", "navigator", "supporting_actor"] as const;

export type DemoRole = (typeof demoRoles)[number];

export function demoSessionPath(role: DemoRole): string {
  return `/v1/demo/session/${role}`;
}
