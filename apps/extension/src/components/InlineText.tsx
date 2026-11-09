/** Render the small inline emphasis used by answers; never interpret HTML. */
export function InlineText({ text }: { text: string }) {
  return (
    <>
      {text
        .split(/(\*\*[^*\n]+\*\*)/g)
        .map((part, index) =>
          part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
            <strong key={index}>{part.slice(2, -2)}</strong>
          ) : (
            part
          ),
        )}
    </>
  );
}
