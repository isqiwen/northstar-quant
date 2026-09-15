import { describe, expect, it } from "vitest";
import research from "../../apps/research/api/protocol.json";
import researchCodec from "../../apps/research/api/codec";
import live from "../../apps/live/api/protocol.json";
import liveCodec from "../../apps/live/api/codec";
import {
  registerProtocol,
  decodeResponse,
  encodeRequest,
} from "../../shared/protobuf";

// Bytes serialized by the generated Python messages, not the browser codec.
const horizon =
  "CAEQABoWChJGQUNUT1JfVU5BVkFJTEFCTEUQAyITSU5TVUZGSUNJRU5UX1NBTVBMRfJ/B3BlYXJzb27yfwhzcGVhcm1hbvJ/FWdyb3VwX2NoYW5nZV9mcmFjdGlvbg==";
const broker = "CgxESVNDT05ORUNURUQSABoLCgdlbmFibGVkEAAaCwoHYmxvY2tlZBABKgA=";
const response = (bytes: string) =>
  new Response(
    Uint8Array.from(atob(bytes), (character) => character.charCodeAt(0)),
    { headers: { "content-type": "application/protobuf" } },
  );

describe("cross-language scalar map facts", () => {
  it("preserves Python integer exclusion counts and rejects unsafe integers", async () => {
    registerProtocol(
      {
        ...research,
        methods: [
          {
            method: "POST",
            path: "/test/horizon",
            input: "northstar.research.FactorHorizon",
            output: "northstar.research.FactorHorizon",
            runtime: false,
          },
        ],
      },
      researchCodec,
    );
    const value = await decodeResponse(
      "POST",
      "/test/horizon",
      response(horizon),
    );
    expect(value).toMatchObject({
      samples: 0,
      excluded: { FACTOR_UNAVAILABLE: 3 },
      pearson: null,
    });
    expect(() =>
      encodeRequest("POST", "/test/horizon", {
        ...(value as object),
        excluded: { FACTOR_UNAVAILABLE: Number.MAX_SAFE_INTEGER + 1 },
      }),
    ).toThrow("整数超出");
  });
  it("preserves both false and true execution flags from Python", async () => {
    registerProtocol(
      {
        ...live,
        methods: [
          {
            method: "GET",
            path: "/test/broker",
            input: "northstar.live.BrokerStatus",
            output: "northstar.live.BrokerStatus",
            runtime: false,
          },
        ],
      },
      liveCodec,
    );
    expect(
      await decodeResponse("GET", "/test/broker", response(broker)),
    ).toMatchObject({
      connection: "DISCONNECTED",
      execution: { enabled: false, blocked: true },
    });
  });
  it("keeps exact Python broker P&L separate from unknown money", async () => {
    registerProtocol(
      {
        ...live,
        methods: [
          {
            method: "GET",
            path: "/test/accounting",
            input: "northstar.web.Empty",
            output: "northstar.live.BrokerAccountProjection",
            runtime: false,
          },
        ],
      },
      liveCodec,
    );
    const value = await decodeResponse(
      "GET",
      "/test/accounting",
      response(
        "CgpJTkNPTVBMRVRFEglzeW50aGV0aWMaFjEwMC4wMTAwMDAwMDAwMDAwMDAwMDEyAWEyAWI4AvJ/BGNhc2jyfwp0b3RhbF9mZWVz",
      ),
    );
    expect(value).toMatchObject({
      status: "INCOMPLETE",
      realized_pnl_before_fees: "100.010000000000000001",
      cash: null,
      total_fees: null,
      fill_count: 2,
      pending_fee_fill_ids: ["a", "b"],
    });
  });
});
