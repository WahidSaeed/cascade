import { render, screen } from "@testing-library/react";
import ConsolePage from "@/app/page";

describe("skeleton", () => {
  it("renders the console page", () => {
    render(<ConsolePage />);
    expect(screen.getByText("Console")).toBeInTheDocument();
  });
});
