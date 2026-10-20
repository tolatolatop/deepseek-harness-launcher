import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";

test("admin setup and member workspace journey", async ({ page, browser }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/launcher/");
  mkdirSync("../docs/screenshots", { recursive: true });
  await page.screenshot({
    path: "../docs/screenshots/login.png",
    fullPage: true,
  });
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "欢迎回来，管理员" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "管理后台" }).click();
  await page.getByRole("button", { name: "环境与模型" }).click();
  await page.getByLabel("API 基础地址").fill("http://gateway.example/v1");
  await page.getByLabel("可选模型").fill("team-model\nteam-reasoning");
  await page.getByRole("button", { name: "保存团队配置" }).click();
  await expect(page.getByRole("status")).toContainText("团队配置已保存");
  await page.getByRole("button", { name: "团队成员", exact: true }).click();
  await page.getByRole("button", { name: "添加成员" }).click();
  await page.getByLabel("用户名", { exact: true }).fill("linzhou");
  await page.getByLabel("显示名称").fill("林舟");
  await page.getByLabel("初始密码").fill("member-browser-password");
  await page.getByRole("button", { name: "创建成员" }).click();
  await expect(page.getByRole("cell", { name: "林舟 linzhou" })).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.getByLabel("用户名", { exact: true }).fill("linzhou");
  await page
    .getByLabel("密码", { exact: true })
    .fill("member-browser-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "开通我的工作空间" }).click();
  await expect(
    page.getByRole("heading", { name: "空间设置", exact: true }),
  ).toBeVisible();
  await page.getByLabel("默认模型").selectOption("team-reasoning");
  await page.getByLabel("模型调用令牌").fill("synthetic-browser-test-token");
  await page
    .getByLabel("个人指令")
    .fill("请优先解释思路，生成文件后说明保存位置。");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByRole("status")).toContainText("设置已保存");
  await page.reload();
  await expect(page.getByLabel("默认模型")).toHaveValue("team-reasoning");
  await expect(page.getByLabel("模型调用令牌")).toHaveValue("");
  await page.getByRole("link", { name: "我的工作空间" }).click();
  await page.getByLabel("上传文件").setInputFiles({
    name: "任务资料.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("日期,销售额\n2026-10-20,1200\n"),
  });
  await expect(page.getByText("任务资料.csv", { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载 任务资料.csv" }).click();
  expect((await download).suggestedFilename()).toBe("任务资料.csv");
  await page.getByRole("button", { name: "关闭提示" }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../docs/screenshots/workspace.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "启动工作空间" }).click();
  await expect(page.getByRole("button", { name: "正在启动" })).toBeDisabled();
  expect(errors).toEqual([]);

  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const mobilePage = await mobile.newPage();
  await mobilePage.goto("/launcher/");
  await expect(
    mobilePage.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
  expect(
    await mobilePage.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await mobilePage.getByLabel("用户名", { exact: true }).fill("linzhou");
  await mobilePage
    .getByLabel("密码", { exact: true })
    .fill("member-browser-password");
  await mobilePage.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    mobilePage.getByRole("heading", { name: "欢迎回来，林舟" }),
  ).toBeVisible();
  expect(
    await mobilePage.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await mobilePage.screenshot({
    path: "../docs/screenshots/mobile.png",
    fullPage: true,
  });
  await mobilePage.getByRole("button", { name: "退出登录" }).click();
  await expect(
    mobilePage.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
  await mobile.close();
});
