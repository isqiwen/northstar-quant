"use client";
import { DatasetList } from "../../../../shared/datasets";
import { query } from "../../api/client";
export default function Page() {
  return <DatasetList read={() => query("/api/datasets")} />;
}
