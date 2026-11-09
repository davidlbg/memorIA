import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { InlineText } from "./InlineText";

it("renders model emphasis as text and never executes embedded HTML", () => {
  const { container } = render(
    <p>
      <InlineText text={"Restam **R$ 30 mil**. <img src=x onerror=alert(1)>"} />
    </p>,
  );
  expect(container.querySelector("strong")).toHaveTextContent("R$ 30 mil");
  expect(container).toHaveTextContent("<img src=x onerror=alert(1)>");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
