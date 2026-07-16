const CONTENT_MARKER = "\n## Content\n";

/**
 * Review packets contain an audit header followed by the actual Markdown note.
 * The workspace renders only the human-facing note; identifiers remain in the
 * collapsed technical inspector and the full packet is still available there.
 */
export function humanReviewMarkdown(packet: string): string {
  let content = packet;
  if (packet.startsWith("# Review:") && packet.includes(CONTENT_MARKER)) {
    content = packet.slice(packet.indexOf(CONTENT_MARKER) + CONTENT_MARKER.length);
  }
  return content.trimStart().replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trim();
}
