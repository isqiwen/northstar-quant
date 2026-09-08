"use client";
import { LiveRecord } from "../../../../overview";
export default function Page() {
  return (
    <LiveRecord title="固定开仓预算" endpoint="/api/broker/opening-budgets" />
  );
}
