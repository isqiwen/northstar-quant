"use client";
import { DatasetDetail } from "../../../../../shared/datasets";
import { query } from "../../../api/client";
export default function Page() {
  return <DatasetDetail read={(id) => query(`/api/datasets/${id}`)} />;
}
