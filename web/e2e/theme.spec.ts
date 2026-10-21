import { test, expect } from "@playwright/test";

test("appearance follows the system, persists and covers admin and mobile", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/launcher/");
  const root = page.locator("html");
  await expect(root).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("combobox", { name: "外观" })).toHaveValue(
    "system",
  );
  await page.screenshot({
    path: testInfo.outputPath("login-dark.png"),
    fullPage: true,
  });

  await page.getByRole("combobox", { name: "外观" }).selectOption("light");
  await expect(root).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(root).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("combobox", { name: "外观" })).toHaveValue(
    "light",
  );

  await page.getByRole("combobox", { name: "外观" }).selectOption("system");
  await expect(root).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(root).toHaveAttribute("data-theme", "light");
  await page.getByRole("combobox", { name: "外观" }).selectOption("dark");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "欢迎回来，管理员" }),
  ).toBeVisible();
  await expect(root).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("combobox", { name: "外观" })).toHaveValue(
    "dark",
  );
  await page.screenshot({
    path: testInfo.outputPath("workspace-dark.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "管理后台" }).click();
  await page.getByRole("button", { name: "添加成员" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCSS(
    "background-color",
    "rgb(25, 37, 54)",
  );
  await page.screenshot({
    path: testInfo.outputPath("admin-dark.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("combobox", { name: "外观" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(root).toHaveAttribute("data-theme", "dark");
  await page.screenshot({
    path: testInfo.outputPath("mobile-dark.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
