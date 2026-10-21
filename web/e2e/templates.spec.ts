import { test, expect } from "@playwright/test";

test("template editor stays usable across screen sizes and preserves invalid drafts", async ({
  page,
}, testInfo) => {
  await page.goto("/launcher/");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("link", { name: "管理后台" }).click();
  await page.getByRole("button", { name: "运行模板", exact: true }).click();
  const create = page.getByRole("button", { name: "新建模板", exact: true });
  await create.click();
  const editor = page.getByRole("form", { name: "新建模板", exact: true });
  const slug = page.getByLabel("模板标识", { exact: true });
  await expect(page.getByLabel("环境名称", { exact: true })).toBeFocused();
  await expect(page.locator(".template-grid")).toHaveCount(0);
  await slug.fill("layout-draft");
  await page.getByLabel("版本号", { exact: true }).fill("1.0.0");
  await page.getByLabel("环境名称", { exact: true }).fill("布局检查环境");
  await page
    .getByLabel("镜像地址与摘要", { exact: true })
    .fill(
      "registry.example/teams/development/harness@sha256:" + "a".repeat(64),
    );
  await page.getByText("环境与启动选项", { exact: false }).click();
  await page
    .getByLabel("公开环境变量（JSON）", { exact: false })
    .fill('{"TZ":"Asia/Shanghai"}');

  for (const width of [1440, 768, 390, 360]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ["light", "dark"]) {
      await page.getByLabel("外观", { exact: true }).selectOption(theme);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const layout = await editor.evaluate((form) => {
        const bounds = form.getBoundingClientRect();
        const fields = [
          ...form.querySelectorAll("input, textarea, button"),
        ].map((field) => field.getBoundingClientRect());
        return {
          overflow: document.documentElement.scrollWidth > window.innerWidth,
          outsideForm: fields.some(
            (field) => field.left < bounds.left || field.right > bounds.right,
          ),
          resources: [
            ...form.querySelectorAll(".template-resource-grid input"),
          ].map((field) => field.getBoundingClientRect().top),
        };
      });
      expect(layout.overflow).toBe(false);
      expect(layout.outsideForm).toBe(false);
      if (width === 1440) expect(new Set(layout.resources).size).toBe(1);
      if (width <= 390) expect(new Set(layout.resources).size).toBe(3);
      if (width === 1440 || (width === 390 && theme === "dark")) {
        await page.screenshot({
          path: testInfo.outputPath(`template-editor-${width}-${theme}.png`),
          fullPage: true,
        });
      }
    }
  }
  await page
    .getByLabel("公开环境变量（JSON）", { exact: false })
    .fill("{invalid");
  await page.getByRole("button", { name: "保存模板草稿" }).click();
  await expect(editor.getByRole("alert")).toHaveText(
    "环境变量需要填写有效 JSON 对象",
  );
  await expect(editor.getByRole("alert")).toBeInViewport();
  await expect(slug).toHaveValue("layout-draft");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(create).toBeFocused();
  await create.click();
  await expect(slug).toHaveValue("");
  await expect(editor.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "返回模板列表", exact: true }).click();
  await expect(create).toBeFocused();
});

test("publish templates, select a version, configure maintenance and request upgrade", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  async function login(username: string, password: string) {
    await page.getByLabel("用户名", { exact: true }).fill(username);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(
      page.getByRole("link", { name: "我的工作空间" }),
    ).toBeVisible();
  }
  await page.goto("/launcher/");
  await login("admin", "browser-test-password");
  await page.getByRole("link", { name: "管理后台" }).click();
  await page.getByRole("button", { name: "运行模板", exact: true }).click();
  await page.getByRole("button", { name: "新建模板", exact: true }).click();
  await page.getByLabel("模板标识", { exact: true }).fill("python-ui");
  await page.getByLabel("版本号", { exact: true }).fill("1.0.0");
  await page.getByLabel("环境名称", { exact: true }).fill("Python 测试环境");
  await page
    .getByLabel("镜像地址与摘要", { exact: true })
    .fill("example/harness@sha256:" + "a".repeat(64));
  await page.getByLabel("默认 CPU", { exact: true }).fill("1");
  await page.getByRole("button", { name: "保存模板草稿" }).click();
  const first = page
    .locator(".template-card")
    .filter({ hasText: "Python 测试环境" })
    .filter({ hasText: "1.0.0" });
  await expect(first.getByText("草稿", { exact: true })).toBeVisible();
  await first.getByRole("button", { name: "编辑草稿" }).click();
  await expect(page.getByLabel("版本号", { exact: true })).toHaveValue("1.0.0");
  await expect(page.getByLabel("默认 CPU", { exact: true })).toHaveValue("1");
  await page.getByRole("button", { name: "保存模板草稿" }).click();
  await expect(first.getByText("草稿", { exact: true })).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await first.getByRole("button", { name: "发布", exact: true }).click();
  await expect(first.getByText("已发布", { exact: true })).toBeVisible();
  await first.getByRole("button", { name: "创建新版本" }).click();
  await expect(page.getByLabel("模板标识", { exact: true })).toHaveValue(
    "python-ui",
  );
  await expect(page.getByLabel("版本号", { exact: true })).toHaveValue("");
  await page.getByLabel("版本号", { exact: true }).fill("2.0.0");
  await page.getByRole("button", { name: "保存模板草稿" }).click();
  const second = page
    .locator(".template-card")
    .filter({ hasText: "Python 测试环境" })
    .filter({ hasText: "2.0.0" });
  page.once("dialog", (dialog) => dialog.accept());
  await second.getByRole("button", { name: "发布", exact: true }).click();
  await expect(second.getByText("已发布", { exact: true })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("templates.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "团队成员", exact: true }).click();
  await page.getByRole("button", { name: "添加成员" }).click();
  await page.getByLabel("用户名", { exact: true }).fill("template-user");
  await page.getByLabel("显示名称", { exact: true }).fill("模板用户");
  await page
    .getByLabel("初始密码", { exact: true })
    .fill("template-user-password");
  await page.getByRole("button", { name: "创建成员", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "模板用户 template-user" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await login("template-user", "template-user-password");
  const selectedId = await page
    .getByRole("option", { name: /Python 测试环境 · 1.0.0/ })
    .getAttribute("value");
  await page
    .getByLabel("运行环境模板", { exact: true })
    .selectOption(selectedId!);
  await page.getByRole("button", { name: "开通我的工作空间" }).click();
  await expect(page.locator(".workspace-title")).toContainText(
    "Python 测试环境 · 1.0.0",
  );
  await page
    .getByRole("link", { name: "空间设置", exact: true })
    .first()
    .click();
  const newId = await page
    .getByRole("option", { name: "Python 测试环境 · 2.0.0", exact: true })
    .getAttribute("value");
  await page.getByLabel("切换环境模板", { exact: true }).selectOption(newId!);
  await page.getByRole("button", { name: "保存环境选择" }).click();
  await expect(page.getByRole("status")).toContainText("环境模板已保存");
  await page
    .getByLabel("每次启动的最长运行时间", { exact: true })
    .selectOption("60");
  await expect(page.getByRole("status")).toContainText("运行时长策略已保存");
  await page.reload();
  await expect(page.getByLabel("切换环境模板", { exact: true })).toHaveValue(
    newId!,
  );
  await expect(
    page.getByLabel("每次启动的最长运行时间", { exact: true }),
  ).toHaveValue("60");
  await page.screenshot({
    path: testInfo.outputPath("environment.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "退出登录" }).click();
  await login("admin", "browser-test-password");
  await page.getByRole("link", { name: "管理后台" }).click();
  await page.getByRole("button", { name: "运行空间", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .getByRole("combobox", { name: "升级 模板用户的工作空间" })
    .selectOption(selectedId!);
  await expect(page.getByRole("status")).toContainText("环境升级已提交");
  await page
    .getByRole("checkbox", { name: "选择 模板用户的工作空间", exact: true })
    .check();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "批量停止", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("已有操作正在进行");
  expect(errors).toEqual([]);
});
