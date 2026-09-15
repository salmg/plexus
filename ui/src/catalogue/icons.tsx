/**
 * Node icons.
 *
 * Hand-drawn rather than pulled from an icon font, for two reasons. This is a
 * tool that may well run on a machine with no route to a CDN, and a stroke
 * glyph on a 16-unit grid is legible at the 14px the node cards actually use —
 * which most icon sets are not.
 *
 * Each one says what the node *is* rather than what it does, because that is
 * what you scan a palette for: a phone is a phone, a file is a file, waves
 * mean radio. They inherit `currentColor`, so a node's class stripe colour
 * carries through without a second token.
 */

import type { ReactElement } from "react";

export type IconName =
  | "sim"
  | "emulate"
  | "window"
  | "agent"
  | "probe"
  | "contact"
  | "field"
  | "phone"
  | "link"
  | "file"
  | "swap"
  | "inject"
  | "eye"
  | "clock"
  | "pause"
  | "chevron"
  | "node";

const PATHS: Record<IconName, ReactElement> = {
  // A SIM, corner cut, contact pad in the middle.
  sim: (
    <>
      <path d="M4 2.5h5.2L12 5.3v8.2H4z" />
      <path d="M6.4 6.6h3.2v3.4H6.4z" />
    </>
  ),
  // A card, answering outward: it is pretending to be one.
  emulate: (
    <>
      <path d="M1.8 4.6h6.4v6.8H1.8z" />
      <path d="M10.6 5.6a4 4 0 0 1 0 4.8" />
      <path d="M12.9 3.9a7 7 0 0 1 0 8.2" />
    </>
  ),
  // A window: a card offered to whatever else is on this machine.
  window: (
    <>
      <path d="M2 3.6h12v8.8H2z" />
      <path d="M2 6.4h12" />
    </>
  ),
  agent: (
    <>
      <path d="M8 2.2v11.6M3.2 5.1l9.6 5.8M12.8 5.1 3.2 10.9" />
    </>
  ),
  probe: (
    <>
      <circle cx="7" cy="7" r="4.2" />
      <path d="M10.1 10.1 13.6 13.6" />
    </>
  ),
  // A contact card, chip on the left where it really sits.
  contact: (
    <>
      <path d="M1.6 4.2h12.8v7.6H1.6z" />
      <path d="M4 6.6h2.8v2.8H4z" />
    </>
  ),
  // Radio, reading: arcs opening away from the reader.
  field: (
    <>
      <path d="M3.2 6.6a2.4 2.4 0 0 1 0 2.8" />
      <path d="M5.9 4.8a5 5 0 0 1 0 6.4" />
      <path d="M8.6 3.1a7.6 7.6 0 0 1 0 9.8" />
    </>
  ),
  phone: (
    <>
      <path d="M5 1.8h6v12.4H5z" />
      <path d="M7 3.4h2" />
    </>
  ),
  // A chain: the card is somewhere else.
  link: (
    <>
      <path d="M6.4 9.6 9.6 6.4" />
      <path d="M5.2 7.8 4 9a2.4 2.4 0 0 0 3.4 3.4l1.2-1.2" />
      <path d="M10.8 8.2 12 7a2.4 2.4 0 0 0-3.4-3.4L7.4 4.8" />
    </>
  ),
  file: (
    <>
      <path d="M4 2h5.2L12 4.8v9.2H4z" />
      <path d="M9.2 2v2.8H12" />
    </>
  ),
  // Two arrows passing: bytes go in, different bytes come out.
  swap: (
    <>
      <path d="M2.8 6h9.6M10.6 4.2 12.4 6l-1.8 1.8" />
      <path d="M13.2 10H3.6M5.4 8.2 3.6 10l1.8 1.8" />
    </>
  ),
  // An extra command arriving from outside the conversation.
  inject: (
    <>
      <path d="M2 6h12" />
      <path d="M8 13.6V7" />
      <path d="M5.6 9.4 8 7l2.4 2.4" />
    </>
  ),
  eye: (
    <>
      <path d="M1.6 8S4 3.8 8 3.8 14.4 8 14.4 8 12 12.2 8 12.2 1.6 8 1.6 8z" />
      <circle cx="8" cy="8" r="1.8" />
    </>
  ),
  clock: (
    <>
      <circle cx="8" cy="8" r="5.8" />
      <path d="M8 4.8v3.4l2.4 1.6" />
    </>
  ),
  pause: (
    <>
      <path d="M6 3.6v8.8M10 3.6v8.8" />
    </>
  ),
  // A disclosure chevron, drawn pointing right. It is rotated in CSS to point
  // down when the thing it heads is open, so one path serves both states.
  chevron: (
    <>
      <path d="M6 4l4 4-4 4" />
    </>
  ),
  // The fallback, for a node type this build does not recognise.
  node: (
    <>
      <circle cx="8" cy="8" r="2.2" />
      <path d="M1.8 8h4M10.2 8h4" />
    </>
  ),
};

interface IconProps {
  name: IconName | undefined;
  size?: number;
  className?: string;
}

export function Icon({ name, size = 14, className }: IconProps) {
  return (
    <svg
      className={className ? `icon ${className}` : "icon"}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name ?? "node"] ?? PATHS.node}
    </svg>
  );
}
