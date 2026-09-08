"use client";
import { LiveRecord } from "../../../../overview";
export default function Page() {
  return (
    <LiveRecord title="确认资金与费用" endpoint="/api/broker/funds-entries" />
  );
}
