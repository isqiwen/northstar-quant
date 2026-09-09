import { NextRequest } from "next/server";
import { forward } from "../../../../../shared/proxy";
export const dynamic = "force-dynamic";
export const GET = (request: NextRequest) =>
  forward(request, ["research.local", "research.wangqiwen.me"], true);
export const POST = GET;
