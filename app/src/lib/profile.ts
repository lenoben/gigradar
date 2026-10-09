const DEFAULT_HEADING = "My work";
const MIN_CHARS = 20;

export interface NormalizedProfile {
  markdown: string;
  /** True when the text had no "## " heading and one was added. */
  wrapped: boolean;
}

/** Strip a code fence (Claude replies in one), and make sure there is at least one "## " skill-area heading. */
export function normalizeProfile(text: string): NormalizedProfile {
  let body = text.replace(/\r\n/g, "\n").trim();
  const fenced = body.match(/^```[a-zA-Z]*\n([\s\S]*?)\n?```$/);
  if (fenced) body = fenced[1].trim();
  if (/^## \S/m.test(body)) return { markdown: body + "\n", wrapped: false };
  return { markdown: `## ${DEFAULT_HEADING}\n${body}\n`, wrapped: true };
}

export function profileLongEnough(text: string): boolean {
  return text.trim().length >= MIN_CHARS;
}
