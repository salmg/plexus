/**
 * Why a reader picker is empty, from the frame to the line under the picker.
 *
 * An SCM SCR3310 listed by `pcsc_scan` on Ubuntu showed as an empty picker
 * that said only "No pcsc device is visible to the runtime", because pyscard
 * was not in the Python running Plexus. The runtime now says which piece is
 * missing; these pin that the editor carries it to the picker, and can ask
 * again without a reload.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import type { Device } from "../src/graph/types";
import { deviceNotes } from "../src/panels/deviceNotes";
import { MockRuntime } from "../src/runtime/mock";
import type { RuntimeHandlers } from "../src/runtime/transport";
import { WebSocketRuntime } from "../src/runtime/ws";
import { useGraph } from "../src/store/useGraph";

const SCR: Device = {
  id: "pcsc:SCM Microsystems Inc. SCR 3310 [CCID Interface] 00 00",
  kind: "pcsc",
  name: "SCM Microsystems Inc. SCR 3310 [CCID Interface] 00 00",
  host: "",
  available: true,
  detail: "contact",
};

const NO_PYSCARD =
  "pyscard is not installed in the Python running Plexus (/home/me/plexus/.venv/bin/python).";

function handlers(overrides: Partial<RuntimeHandlers> = {}): RuntimeHandlers {
  return {
    onState: () => {},
    onNode: () => {},
    onEdge: () => {},
    onApdu: () => {},
    onLog: () => {},
    onDevices: () => {},
    onUnavailable: () => {},
    ...overrides,
  };
}

const BOARD: Device = {
  id: "simtrace2:1-2.3",
  kind: "simtrace2",
  name: "SimTrace2 (USB 1-2.3)",
  host: "",
  available: true,
  detail: "simtrace2-remsim starts after Run",
};

const NO_BOARD =
  "No SimTrace2 on USB (vendor 1d50, product 60e3). Plug the board in, and check that this VM or container is given the USB device.";

describe("the line under a picker", () => {
  it("shows the runtime's reason for a PC/SC picker", () => {
    expect(deviceNotes([], NO_PYSCARD, "pyscard in this Python")).toEqual({
      empty: { key: "field.noDevices" },
      problem: NO_PYSCARD,
    });
  });

  it("says nothing extra once a reader is there", () => {
    expect(deviceNotes([SCR], undefined, undefined)).toEqual({ empty: null, problem: "" });
  });

  it("still shows a reason with a device listed", () => {
    // The runtime decides what is a problem; a listed device does not hide it.
    expect(deviceNotes([BOARD], NO_BOARD, undefined)).toEqual({
      empty: null,
      problem: NO_BOARD,
    });
  });

  it("says a node is not built rather than that nothing was found", () => {
    const needs = "pyscard, an ACR122U, and atrium's PN532 emulator";
    expect(deviceNotes([], undefined, needs)).toEqual({
      empty: { key: "field.notBuilt", needs },
      problem: "",
    });
  });

  it("falls back to the plain line when the runtime names no need", () => {
    // The simulator: everything binds, so no need is named.
    expect(deviceNotes([], undefined, undefined)).toEqual({
      empty: { key: "field.noDevices" },
      problem: "",
    });
  });
});

describe("the store", () => {
  it("keeps the reasons with the list, and clears them with the next list", () => {
    useGraph.getState().setDevices([], { pcsc: NO_PYSCARD, simtrace2: NO_BOARD });
    expect(useGraph.getState().devicesProblems).toEqual({
      pcsc: NO_PYSCARD,
      simtrace2: NO_BOARD,
    });

    useGraph.getState().setDevices([SCR, BOARD]);
    expect(useGraph.getState().devices).toEqual([SCR, BOARD]);
    expect(useGraph.getState().devicesProblems).toEqual({});
  });
});

describe("asking again", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("the simulator answers with the same devices", async () => {
    const onDevices = vi.fn();
    const runtime = new MockRuntime();
    await runtime.connect(handlers({ onDevices }));
    onDevices.mockClear();

    runtime.refreshDevices();
    expect(onDevices).toHaveBeenCalledTimes(1);
    runtime.disconnect();
  });

  it("a runtime gets a devices frame, and its reason reaches the handler", async () => {
    const sockets: FakeSocket[] = [];
    class FakeSocket {
      static OPEN = 1;
      readyState = 1;
      sent: unknown[] = [];
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      onclose: ((event: { code: number }) => void) | null = null;
      constructor() {
        sockets.push(this);
        queueMicrotask(() => this.onopen?.());
      }
      send(data: string) {
        this.sent.push(JSON.parse(data));
      }
      close() {}
    }
    vi.stubGlobal("WebSocket", FakeSocket);

    const onDevices = vi.fn();
    const runtime = new WebSocketRuntime("ws://127.0.0.1:8900/ws");
    await runtime.connect(handlers({ onDevices }));
    const socket = sockets[0];

    socket.onmessage?.({
      data: JSON.stringify({ type: "devices", devices: [], problems: { pcsc: NO_PYSCARD } }),
    });
    expect(onDevices).toHaveBeenLastCalledWith([], { pcsc: NO_PYSCARD });

    runtime.refreshDevices();
    expect(socket.sent.at(-1)).toEqual({ type: "devices" });

    // An older runtime sends no reason; the handler gets none.
    socket.onmessage?.({ data: JSON.stringify({ type: "devices", devices: [SCR] }) });
    expect(onDevices).toHaveBeenLastCalledWith([SCR], undefined);

    runtime.disconnect();
  });

  it("asking while disconnected is quiet", () => {
    const runtime = new WebSocketRuntime("ws://127.0.0.1:8900/ws");
    expect(() => runtime.refreshDevices()).not.toThrow();
  });
});
