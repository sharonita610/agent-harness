/** 답변용 최소 마크다운: **굵게**, `코드`, 줄바꿈만. 라이브러리 없이. */
export default function Rich({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, i) => (
        <span key={i}>
          {line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, j) => {
            if (part.startsWith("**") && part.endsWith("**")) return <strong key={j}>{part.slice(2, -2)}</strong>;
            if (part.startsWith("`") && part.endsWith("`")) return <code key={j}>{part.slice(1, -1)}</code>;
            return <span key={j}>{part}</span>;
          })}
          {i < lines.length - 1 && <br />}
        </span>
      ))}
    </>
  );
}
