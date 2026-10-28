/** 답변용 최소 마크다운: **굵게**, *기울임*, `코드`, 제목(#), 글머리(-·*), 표(|). 라이브러리 없이. */
import type { ReactNode } from "react";

function renderInline(text: string, keyBase: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|(?<![*\w])\*[^*\s][^*]*\*(?![*\w]))/g).map((part, j) => {
    const key = `${keyBase}-${j}`;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={key}>{part.slice(1, -1)}</code>;
    if (part.length > 2 && part.startsWith("*") && part.endsWith("*"))
      return <em key={key}>{part.slice(1, -1)}</em>;
    return <span key={key}>{part}</span>;
  });
}
const splitCells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
const isSeparatorRow = (line: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

export default function Rich({ text }: { text: string }) {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("|") && i + 1 < lines.length && isSeparatorRow(lines[i + 1])) {
      const head = splitCells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(splitCells(lines[i]));
        i++;
      }
      out.push(
        <table className="md" key={`t${i}`}>
          <thead>
            <tr>
              {head.map((h, k) => (
                <th key={k}>{renderInline(h, `h${i}-${k}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>
                {r.map((c, k) => (
                  <td
                    key={k}
                    className={
                      /^[\s\d.,+\-−%a-zA-ZΩµ]*\d[\s\d.,+\-−%a-zA-ZΩµ]*$/.test(c) &&
                      /\d/.test(c) &&
                      c.length < 16
                        ? "num"
                        : ""
                    }
                  >
                    {renderInline(c, `c${i}-${ri}-${k}`)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      continue;
    }
    if (/^\s*[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*•]\s+/, ""));
        i++;
      }
      out.push(
        <ul className="md" key={`u${i}`}>
          {items.map((it, k) => (
            <li key={k}>{renderInline(it, `l${i}-${k}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (/^#{1,4}\s+/.test(line)) {
      out.push(
        <p className="md-h" key={`h${i}`}>
          {renderInline(line.replace(/^#{1,4}\s+/, ""), `hh${i}`)}
        </p>,
      );
      i++;
      continue;
    }
    out.push(
      <span key={`p${i}`}>
        {renderInline(line, `p${i}`)}
        {i < lines.length - 1 && <br />}
      </span>,
    );
    i++;
  }
  return <>{out}</>;
}
