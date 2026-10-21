import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Box, Check, ChevronDown, Plus } from "lucide-react";

type Request = (path: string, method?: string, body?: unknown) => Promise<any>;
type Notify = (message: string, error?: boolean) => void;
export type EnvironmentTemplate = {
  id: string;
  slug: string;
  name: string;
  version: string;
  description: string;
  image: string;
  published: boolean;
  enabled: boolean;
  spec: {
    contract_version: string;
    compatibility_group: string;
    cpu: number;
    memory_mb: number;
    tmp_mb: number;
    mount_policy: string;
    environment: Record<string, string>;
    startup_patches: string[];
  };
};
export type WorkspaceRuntime = {
  template_id: string;
  template_name: string;
  template_version: string;
  image: string;
  contract_version: string;
  resources: { cpu: number; memory_mb: number };
  compatibility_group: string;
  applied_template: {
    id: string | null;
    name: string | null;
    version: string | null;
    image: string | null;
  };
  actual_image_id: string;
  actual_image_ref: string;
  checked_at: number;
  max_runtime_minutes: number | null;
  stop_at: number | null;
};

export function TemplateManager({
  templates,
  request,
  reload,
  notify,
}: {
  templates: EnvironmentTemplate[];
  request: Request;
  reload: () => Promise<void>;
  notify: Notify;
}) {
  const [draft, setDraft] = useState<EnvironmentTemplate | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const editorRef = useRef<HTMLFormElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const [formError, setFormError] = useState("");
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) {
      editorRef.current?.scrollIntoView({ block: "start" });
      editorRef.current
        ?.querySelector<HTMLInputElement>("input")
        ?.focus({ preventScroll: true });
    } else if (wasOpen.current) {
      createButtonRef.current?.focus({ preventScroll: true });
      createButtonRef.current?.scrollIntoView({ block: "nearest" });
    }
    wasOpen.current = open;
  }, [open]);
  useEffect(() => {
    if (formError) {
      errorRef.current?.focus({ preventScroll: true });
      errorRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [formError]);
  function openEditor(template: EnvironmentTemplate | null, edit = false) {
    setDraft(template);
    setEditing(edit);
    setFormError("");
    setOpen(true);
  }
  async function perform(task: () => Promise<unknown>, message: string) {
    setBusy(true);
    try {
      await task();
      await reload();
      notify(message);
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {!open && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h3>运行环境模板</h3>
              <small>每个已发布版本保持不可变，空间保存独立快照。</small>
            </div>
            <button
              className="button primary small"
              ref={createButtonRef}
              onClick={() => openEditor(null)}
            >
              <Plus size={15} />
              新建模板
            </button>
          </div>
          <div className="template-grid">
            {templates.map((template) => (
              <article className="template-card" key={template.id}>
                <div className="template-card-heading">
                  <Box size={19} />
                  <h3>{template.name}</h3>
                  <span className="pill">{template.version}</span>
                </div>
                <p>{template.description || "标准持久化项目与会话目录"}</p>
                <code className="image-reference">{template.image}</code>
                <small>
                  {template.spec.cpu} vCPU · {template.spec.memory_mb / 1024} GB
                  · {template.spec.compatibility_group}
                </small>
                <div className="template-card-actions">
                  <span
                    className={`pill ${template.published && template.enabled ? "good" : ""}`}
                  >
                    {!template.enabled
                      ? "已停用"
                      : template.published
                        ? "已发布"
                        : "草稿"}
                  </span>
                  {!template.published && (
                    <button
                      className="text-link"
                      disabled={busy}
                      onClick={() => openEditor(template, true)}
                    >
                      编辑草稿
                    </button>
                  )}
                  {!template.published && (
                    <button
                      className="text-link"
                      disabled={busy}
                      onClick={() => {
                        if (
                          confirm(
                            "发布前请确认镜像已经准备好，并且属于声明的数据兼容组。是否发布？",
                          )
                        )
                          perform(
                            () =>
                              request(
                                `/admin/templates/${template.id}/publish`,
                                "POST",
                              ),
                            "模板已发布",
                          );
                      }}
                    >
                      发布
                    </button>
                  )}
                  <button
                    className="text-link"
                    disabled={busy}
                    onClick={() =>
                      perform(
                        () =>
                          request(`/admin/templates/${template.id}`, "PATCH", {
                            enabled: !template.enabled,
                          }),
                        template.enabled
                          ? "模板已停用，已有空间继续使用原快照"
                          : "模板已启用",
                      )
                    }
                  >
                    {template.enabled ? "停用" : "启用"}
                  </button>
                  <button
                    className="text-link"
                    disabled={busy}
                    onClick={() => openEditor(template)}
                  >
                    创建新版本
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
      {open && (
        <form
          className="panel template-editor"
          ref={editorRef}
          aria-labelledby="template-editor-title"
          onSubmit={async (event) => {
            event.preventDefault();
            setFormError("");
            const data = new FormData(event.currentTarget);
            let environment;
            try {
              environment = JSON.parse(String(data.get("environment") || "{}"));
            } catch {
              setFormError("环境变量需要填写有效 JSON 对象");
              return;
            }
            setBusy(true);
            try {
              await request(
                editing && draft
                  ? `/admin/templates/${draft.id}`
                  : "/admin/templates",
                editing ? "PUT" : "POST",
                {
                  slug: data.get("slug"),
                  name: data.get("name"),
                  version: data.get("version"),
                  description: data.get("description"),
                  image: data.get("image"),
                  spec: {
                    contract_version: "launcher-runtime/v1",
                    compatibility_group: data.get("compatibility_group"),
                    mount_policy: "persistent-project-home",
                    cpu: Number(data.get("cpu")),
                    memory_mb: Number(data.get("memory_mb")),
                    tmp_mb: Number(data.get("tmp_mb")),
                    environment,
                    startup_patches: String(data.get("startup_patches") || "")
                      .split("\n")
                      .map((v) => v.trim())
                      .filter(Boolean),
                  },
                },
              );
              await reload();
              setOpen(false);
              notify("模板草稿已保存，确认后可发布");
            } catch (error) {
              setFormError((error as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <header className="template-editor-heading">
            <div>
              <span className="template-editor-context">
                <Box size={15} aria-hidden="true" />
                运行环境模板 / 草稿
              </span>
              <h3 id="template-editor-title">
                {editing
                  ? "编辑模板草稿"
                  : draft
                    ? "创建模板新版本"
                    : "新建模板"}
              </h3>
              <p>
                {draft && !editing
                  ? `基于 ${draft.name} · ${draft.version} 创建，填写新的版本号后保存。`
                  : "配置团队可选的运行环境，保存草稿后再发布。"}
              </p>
            </div>
            <button
              type="button"
              className="button secondary small"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              <ArrowLeft size={15} aria-hidden="true" />
              返回模板列表
            </button>
          </header>
          <section
            className="template-editor-section"
            aria-labelledby="template-basics-title"
          >
            <div className="template-section-description">
              <h4 id="template-basics-title">基本信息</h4>
              <p>用名称识别环境，用版本区分每次发布。</p>
            </div>
            <div className="template-field-grid">
              <label className="template-field-wide">
                环境名称
                <input
                  name="name"
                  maxLength={60}
                  defaultValue={draft?.name}
                  required
                  placeholder="Python 开发环境"
                />
              </label>
              <label>
                模板标识
                <input
                  name="slug"
                  pattern="[a-z0-9][a-z0-9-]*"
                  maxLength={64}
                  defaultValue={draft?.slug}
                  required
                  placeholder="python-dev"
                />
              </label>
              <label>
                版本号
                <input
                  name="version"
                  defaultValue={editing ? draft?.version : ""}
                  maxLength={32}
                  required
                  placeholder="1.0.0"
                />
              </label>
              <label className="template-field-wide">
                环境说明
                <textarea
                  name="description"
                  rows={2}
                  maxLength={2000}
                  defaultValue={draft?.description}
                />
              </label>
            </div>
          </section>
          <section
            className="template-editor-section"
            aria-labelledby="template-image-title"
          >
            <div className="template-section-description">
              <h4 id="template-image-title">镜像与数据</h4>
              <p>使用固定镜像摘要，确保每次启动使用相同环境。</p>
            </div>
            <div className="template-section-fields">
              <label>
                镜像地址与摘要
                <input
                  name="image"
                  aria-label="镜像地址与摘要"
                  aria-describedby="template-image-help"
                  defaultValue={
                    draft?.image.includes("@sha256:") ? draft.image : ""
                  }
                  required
                  placeholder="registry.example/harness-python@sha256:…"
                  className="template-code"
                  spellCheck={false}
                />
                <small id="template-image-help">
                  镜像需预先准备到 Docker 主机，地址必须包含 @sha256: 摘要。
                </small>
              </label>
              <label>
                数据兼容组
                <input
                  name="compatibility_group"
                  defaultValue={
                    draft?.spec.compatibility_group || "deepseek-harness-0.2.1"
                  }
                  required
                />
                <small>只有兼容组相同的模板才能切换并复用现有会话。</small>
              </label>
            </div>
          </section>
          <section
            className="template-editor-section"
            aria-labelledby="template-resources-title"
          >
            <div className="template-section-description">
              <h4 id="template-resources-title">资源配置</h4>
              <p>设置此环境的默认运行资源。</p>
            </div>
            <div className="template-section-fields">
              <div className="template-resource-grid">
                <label>
                  默认 CPU
                  <input
                    name="cpu"
                    type="number"
                    step="0.5"
                    min="0.5"
                    max="16"
                    defaultValue={draft?.spec.cpu || 2}
                    required
                  />
                </label>
                <label>
                  默认内存（MB）
                  <input
                    name="memory_mb"
                    type="number"
                    min="512"
                    max="32768"
                    defaultValue={draft?.spec.memory_mb || 2048}
                    required
                  />
                </label>
                <label>
                  临时目录限额（MB）
                  <input
                    name="tmp_mb"
                    type="number"
                    min="64"
                    max="2048"
                    defaultValue={draft?.spec.tmp_mb || 512}
                    required
                  />
                </label>
              </div>
              <p className="template-field-help">
                实际 CPU 和内存同时受成员配额限制。项目和 Harness home
                固定持久化，停止和重建时保留。
              </p>
            </div>
          </section>
          <details className="template-advanced">
            <summary>
              <span>
                环境与启动选项 <small>可选</small>
              </span>
              <ChevronDown size={16} aria-hidden="true" />
            </summary>
            <div className="template-field-grid">
              <label>
                公开环境变量（JSON）
                <textarea
                  name="environment"
                  className="template-code"
                  spellCheck={false}
                  rows={5}
                  defaultValue={JSON.stringify(
                    draft?.spec.environment || {},
                    null,
                    2,
                  )}
                />
                <small>
                  用于 LANG、TZ 等公开配置，勿填写凭据。启动器保留变量不能覆盖。
                </small>
              </label>
              <label>
                镜像内启动补丁
                <textarea
                  name="startup_patches"
                  className="template-code"
                  spellCheck={false}
                  rows={5}
                  defaultValue={draft?.spec.startup_patches.join("\n")}
                  placeholder="每行一个 /opt 下的 YAML 文件路径"
                />
              </label>
            </div>
          </details>
          <footer className="template-editor-footer">
            <div>
              <p className="template-field-help">
                草稿保存后可继续编辑，发布后通过新版本迭代。
              </p>
              {formError && (
                <div
                  className="template-form-error"
                  role="alert"
                  tabIndex={-1}
                  ref={errorRef}
                >
                  {formError}
                </div>
              )}
            </div>
            <div className="template-editor-actions">
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                取消
              </button>
              <button type="submit" className="button primary" disabled={busy}>
                <Check size={16} aria-hidden="true" />
                {busy ? "保存中…" : "保存模板草稿"}
              </button>
            </div>
          </footer>
        </form>
      )}
    </>
  );
}

export function WorkspaceEnvironment({
  workspace,
  templates,
  request,
  refresh,
  notify,
  disabled = false,
}: {
  workspace: { id: string; config_version: number; runtime?: WorkspaceRuntime };
  templates: EnvironmentTemplate[];
  request: Request;
  refresh: () => Promise<void>;
  notify: Notify;
  disabled?: boolean;
}) {
  const runtime = workspace.runtime;
  const [selected, setSelected] = useState(runtime?.template_id || "");
  const [busy, setBusy] = useState(false);
  useEffect(
    () => setSelected(runtime?.template_id || ""),
    [runtime?.template_id],
  );
  const available = templates.filter(
    (template) =>
      !runtime ||
      template.spec.compatibility_group === runtime.compatibility_group,
  );
  // The backend enforces compatibility even when the original template is retired.
  const choices = available;
  return (
    <section className="panel settings-panel">
      <div className="section-intro">
        <span className="section-icon">
          <Box />
        </span>
        <div>
          <h3>运行环境</h3>
          <p>选择环境版本和自动停止策略，项目与会话保存在原目录中。</p>
        </div>
      </div>
      {runtime && (
        <div className="environment-info">
          <span>当前选择</span>
          <strong>
            {runtime.template_name} · {runtime.template_version}
          </strong>
          <span>运行版本</span>
          <strong>
            {runtime.actual_image_id
              ? `${runtime.applied_template.name || runtime.template_name} · ${runtime.applied_template.version || runtime.template_version}`
              : "未运行"}
          </strong>
          <span>实际镜像</span>
          <code>{runtime.actual_image_id || "启动后记录"}</code>
          <span>模板镜像</span>
          <code>{runtime.image}</code>
          {runtime.checked_at > 0 && (
            <>
              <span>最近接管检查</span>
              <span>
                {new Date(runtime.checked_at * 1000).toLocaleString("zh-CN")}
              </span>
            </>
          )}
        </div>
      )}
      <div className="form-grid environment-controls">
        <label>
          切换环境模板
          <select
            aria-label="切换环境模板"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            {runtime &&
              !templates.some((t) => t.id === runtime.template_id) && (
                <option value={runtime.template_id}>
                  {runtime.template_name} · {runtime.template_version}（已停用）
                </option>
              )}
            {choices.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name} · {template.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          每次启动的最长运行时间
          <select
            aria-label="每次启动的最长运行时间"
            value={runtime?.max_runtime_minutes || ""}
            disabled={busy || disabled}
            onChange={async (event) => {
              setBusy(true);
              try {
                await request(
                  `/workspaces/${workspace.id}/maintenance`,
                  "PUT",
                  {
                    max_runtime_minutes: event.target.value
                      ? Number(event.target.value)
                      : null,
                  },
                );
                await refresh();
                notify("运行时长策略已保存");
              } catch (error) {
                notify((error as Error).message, true);
              } finally {
                setBusy(false);
              }
            }}
          >
            <option value="">不限时</option>
            {runtime?.max_runtime_minutes &&
              ![15, 60, 240, 480, 1440, 10080].includes(
                runtime.max_runtime_minutes,
              ) && (
                <option value={runtime.max_runtime_minutes}>
                  {runtime.max_runtime_minutes} 分钟
                </option>
              )}
            <option value="15">15 分钟</option>
            <option value="60">1 小时</option>
            <option value="240">4 小时</option>
            <option value="480">8 小时</option>
            <option value="1440">24 小时</option>
            <option value="10080">7 天</option>
          </select>
        </label>
      </div>
      {runtime?.stop_at && (
        <p className="muted">
          计划停止：{new Date(runtime.stop_at * 1000).toLocaleString("zh-CN")}
          。到期会中断运行任务，文件和会话保留。
        </p>
      )}
      <button
        type="button"
        className="button secondary small"
        disabled={
          busy || disabled || !selected || selected === runtime?.template_id
        }
        onClick={async () => {
          setBusy(true);
          try {
            await request(`/workspaces/${workspace.id}/template`, "PUT", {
              template_id: selected,
              expected_version: workspace.config_version,
            });
            await refresh();
            notify("环境模板已保存，重启空间后应用");
          } catch (error) {
            notify((error as Error).message, true);
          } finally {
            setBusy(false);
          }
        }}
      >
        保存环境选择
      </button>
    </section>
  );
}
