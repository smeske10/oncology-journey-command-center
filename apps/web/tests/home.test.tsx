import { render, screen } from "@testing-library/react";
import Home from "../app/page";

test("introduces the synthetic oncology navigation demo", () => {
  render(<Home />);

  expect(
    screen.getByRole("heading", { name: /oncology journey command center/i }),
  ).toBeVisible();
  expect(screen.getByText(/synthetic data only/i)).toBeVisible();
});
