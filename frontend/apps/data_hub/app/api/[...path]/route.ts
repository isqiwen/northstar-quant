import { NextRequest } from "next/server";
import { forward, lanHosts } from "../../../../../shared/proxy";
export const dynamic = "force-dynamic";
export const GET = (request: NextRequest) =>
  forward(request, lanHosts("core.local"));
export const POST = GET;
