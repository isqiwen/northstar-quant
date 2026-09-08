import { mutate, query } from "../../apps/live/api/client";
export function checkCommandTypes() {
  const budget = "/api/streams/s/opening-budgets";
  const control = "/api/streams/s/control";
  const body = {
    sequence: 1,
    order_check_id: "check",
    limit_price: "1234567890.123456789",
    request_id: "request",
  };
  const command = { action: "STOP" as const, request_id: "r" };
  void mutate(budget, body, "runtime");
  // @ts-expect-error Money must remain decimal text.
  void mutate(budget, { ...body, limit_price: 1.2 }, "r");
  // @ts-expect-error Fixed command identity is mandatory.
  void mutate(control, { action: "STOP" }, "r");
  // @ts-expect-error Unknown actions cannot compile.
  void mutate(control, { ...command, action: "RECONNECT" }, "r");
  // @ts-expect-error Management commands cannot grant execution authority.
  void mutate(control, { ...command, authorize: true }, "r");
  // @ts-expect-error Runtime binding is required.
  void mutate(control, command);
  // @ts-expect-error Live does not expose Research operations.
  void query("/api/runs");
  // @ts-expect-error Misspelled response fields cannot compile.
  query("/api/live/status")?.response?.runtim_id;
}
