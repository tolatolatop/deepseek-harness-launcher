import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "@fontsource/manrope/800.css";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Box,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Cpu,
  File,
  Folder,
  HardDrive,
  KeyRound,
  Layers3,
  LoaderCircle,
  LogOut,
  MemoryStick,
  MoreHorizontal,
  Monitor,
  Play,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Square,
  Terminal,
  Upload,
  Users,
  Waves,
  X,
  Zap,
  CircleAlert,
} from "lucide-react";
import "./style.css";
import { ThemeSelect } from "./theme";
import {
  TemplateManager,
  WorkspaceEnvironment,
  type EnvironmentTemplate,
  type WorkspaceRuntime,
} from "./environments";

declare global {
  interface Window {
    __LAUNCHER_BASE__?: string;
  }
}
const injected = window.__LAUNCHER_BASE__ || "";
const BASE = injected.includes("__LAUNCHER") ? "/launcher" : injected;
let csrf = "";
async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const form = body instanceof FormData;
  const response = await fetch(`${BASE}/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
      ...(!form && body !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
    },
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login")
      window.dispatchEvent(new Event("session-expired"));
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : value.detail?.[0]?.msg || `请求失败 (${response.status})`,
    );
  }
  return value;
}
type User = {
  id: string;
  username: string;
  display_name: string;
  role: string;
  active: boolean;
  cpu: number;
  memory_mb: number;
  storage_mb: number;
};
type Workspace = {
  id: string;
  user_id: string;
  name: string;
  status: string;
  error: string;
  model: string;
  has_api_token: boolean;
  language: string;
  instructions: string;
  plugins: string[];
  config_version: number;
  applied_version: number;
  entry_url: string;
  desktop_url?: string | null;
  runtime?: WorkspaceRuntime;
};
type Catalog = {
  templates: EnvironmentTemplate[];
  models: string[];
  gateway_configured: boolean;
  template_name: string;
  worker_online: boolean;
  plugins: {
    id: string;
    name: string;
    description: string;
    editable: boolean;
  }[];
};
type Operation = {
  id: string;
  action: string;
  status: string;
  message: string;
  created_at: number;
};
const statusNames: Record<string, string> = {
  running: "运行中",
  stopped: "已停止",
  starting: "正在启动",
  recovering: "正在恢复连接",
  stopping: "正在停止",
  error: "需要处理",
};
const actionNames: Record<string, string> = {
  start: "启动空间",
  stop: "停止空间",
  restart: "重启空间",
  rebuild: "重建环境",
};
const formatBytes = (size: number) =>
  size >= 1024 ** 3
    ? `${(size / 1024 ** 3).toFixed(1)} GB`
    : size >= 1024 ** 2
      ? `${(size / 1024 ** 2).toFixed(1)} MB`
      : size >= 1024
        ? `${(size / 1024).toFixed(1)} KB`
        : `${size} B`;
const date = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
function State({ value }: { value: string }) {
  return (
    <span className={`status status-${value}`}>
      <i />
      {statusNames[value] || value}
    </span>
  );
}
function Spinner() {
  return <LoaderCircle size={17} className="spin" />;
}
function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(location.hash.slice(1) || "workspace");
  const [ws, setWs] = useState<Workspace | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [ops, setOps] = useState<Operation[]>([]);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const notify = useCallback(
    (text: string, error = false) => setNotice({ text, error }),
    [],
  );
  useEffect(() => {
    api("/auth/me")
      .then((r) => {
        setUser(r.user);
        csrf = r.csrf_token;
      })
      .catch(() => {})
      .finally(() => setLoading(false));
    const expired = () => {
      setUser(null);
      setWs(null);
      setOps([]);
      setCatalog(null);
      csrf = "";
    };
    const navigate = () => setPage(location.hash.slice(1) || "workspace");
    window.addEventListener("session-expired", expired);
    window.addEventListener("hashchange", navigate);
    return () => {
      window.removeEventListener("session-expired", expired);
      window.removeEventListener("hashchange", navigate);
    };
  }, []);
  useEffect(() => {
    if (user && user.role !== "admin" && page === "admin")
      location.hash = "workspace";
  }, [user, page]);
  useEffect(() => {
    if (notice && !notice.error) {
      const timer = setTimeout(() => setNotice(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [notice]);
  const refresh = useCallback(async () => {
    const [space, cat] = await Promise.all([
      api<Workspace | null>("/workspace"),
      api<Catalog>("/catalog"),
    ]);
    setWs(space);
    setCatalog(cat);
    if (space) setOps(await api(`/workspaces/${space.id}/operations`));
  }, []);
  useEffect(() => {
    if (!user) return;
    refresh().catch((e) => notify(e.message, true));
    const timer = setInterval(() => refresh().catch(() => {}), 4000);
    return () => clearInterval(timer);
  }, [user, refresh, notify]);
  const run = async (task: () => Promise<void>) => {
    setBusy(true);
    try {
      await task();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const action = (name: string) =>
    run(async () => {
      if (!ws) return;
      if (
        ["stop", "restart", "rebuild"].includes(name) &&
        !window.confirm(
          `${actionNames[name]}会中断当前执行中的任务。项目文件和会话记录会保留，是否继续？`,
        )
      )
        return;
      await api(`/workspaces/${ws.id}/actions/${name}`, "POST");
      await refresh();
      notify("操作已提交，可以在最近活动中查看进度");
    });
  if (loading)
    return (
      <div className="loading-screen">
        <Waves />
        <span>正在打开工作台</span>
      </div>
    );
  if (!user)
    return (
      <Login
        onLogin={(r) => {
          setUser(r.user);
          setWs(null);
          setOps([]);
          csrf = r.csrf_token;
          setNotice(null);
          location.hash = "workspace";
        }}
      />
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a href="#workspace" className="brand">
          <span className="brand-icon">
            <Waves size={24} />
          </span>
          <span>
            DeepSeek<small>团队工作台</small>
          </span>
        </a>
        <div className="team-label">
          <span className="team-square">W</span>
          <div>
            团队空间<small>成员专属环境</small>
          </div>
          <ShieldCheck size={16} />
        </div>
        <div className="nav-label">工作台</div>
        <nav>
          <a
            className={page === "workspace" ? "selected" : ""}
            href="#workspace"
          >
            <Box size={19} />
            我的工作空间
            <ChevronRight size={15} />
          </a>
          <a className={page === "settings" ? "selected" : ""} href="#settings">
            <Settings2 size={19} />
            空间设置
          </a>
          {user.role === "admin" && (
            <>
              <div className="nav-label admin-label">团队管理</div>
              <a className={page === "admin" ? "selected" : ""} href="#admin">
                <Users size={19} />
                管理后台
              </a>
            </>
          )}
        </nav>
        <div className="sidebar-note">
          <ShieldCheck size={19} />
          <strong>属于你的工作环境</strong>
          <p>停止空间后，文件与会话仍会保留。下次启动，接着完成。</p>
        </div>
        <div className="profile">
          <div className="avatar">{user.display_name[0]}</div>
          <div>
            <strong>{user.display_name}</strong>
            <small>{user.role === "admin" ? "管理员" : "团队成员"}</small>
          </div>
          <button
            className="icon-button"
            aria-label="退出登录"
            onClick={() =>
              run(async () => {
                await api("/auth/logout", "POST");
                setUser(null);
                setWs(null);
              })
            }
          >
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            团队空间
            <ChevronRight size={14} />
            <span>
              {page === "admin"
                ? "管理后台"
                : page === "settings"
                  ? "空间设置"
                  : "我的工作空间"}
            </span>
          </div>
          <span className="topbar-note">
            <span className="privacy-label">
              <span className="dot" />
              私有工作台
            </span>
            <ThemeSelect />
            <button
              className="icon-button mobile-logout"
              aria-label="退出登录"
              onClick={() =>
                run(async () => {
                  await api("/auth/logout", "POST");
                  setUser(null);
                  setWs(null);
                })
              }
            >
              <LogOut size={16} />
            </button>
          </span>
        </header>
        <main>
          {notice && (
            <div
              role={notice.error ? "alert" : "status"}
              className={`notice ${notice.error ? "notice-error" : ""}`}
            >
              {notice.error ? <CircleAlert size={18} /> : <Check size={18} />}
              <span>{notice.text}</span>
              <button
                className="icon-button"
                aria-label="关闭提示"
                onClick={() => setNotice(null)}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {catalog && !catalog.worker_online && (
            <div className="notice notice-warning">
              <CircleAlert size={18} />
              运行服务尚未连接。设置和文件仍可访问，启动操作将在服务恢复后处理。
            </div>
          )}
          {page === "workspace" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">YOUR WORKSPACE</div>
                  <h1>欢迎回来，{user.display_name}</h1>
                  <p>带上你的任务，在专属环境里继续工作。</p>
                </div>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => run(refresh)}
                >
                  <RefreshCw size={16} />
                  刷新状态
                </button>
              </div>
              <div className="workspace-panel">
                <div className="workspace-stripe" />
                <div className="workspace-main">
                  <div className="workspace-title">
                    <span className="workspace-symbol">
                      <Terminal size={28} />
                    </span>
                    <div>
                      <h2>{ws?.name || "你的第一个工作空间"}</h2>
                      <p>
                        {ws?.runtime?.template_name ||
                          catalog?.template_name ||
                          "标准开发环境"}
                        {ws?.runtime && ` · ${ws.runtime.template_version}`}
                        <span className="inline-divider" />
                        DeepSeek Harness
                      </p>
                    </div>
                    {ws && <State value={ws.status} />}
                  </div>
                  <p className="workspace-description">
                    {!ws
                      ? "开通并启动你的工作空间，模型连接可以稍后配置。"
                      : ws.status === "running"
                        ? "环境已就绪。打开 DeepSeek，处理资料、编写代码，或继续上次的任务。"
                        : "项目文件与会话保存在你的专属存储中，启动空间即可继续。"}
                  </p>
                  {ws?.error && (
                    <div className="inline-error">
                      <CircleAlert size={17} />
                      {ws.error}
                    </div>
                  )}
                  {!ws && (
                    <label className="create-template-select">
                      运行环境模板
                      <select
                        aria-label="运行环境模板"
                        value={
                          selectedTemplate || catalog?.templates[0]?.id || ""
                        }
                        onChange={(event) =>
                          setSelectedTemplate(event.target.value)
                        }
                      >
                        {!catalog?.templates.length && (
                          <option value="">暂无已发布模板，请联系管理员</option>
                        )}
                        {catalog?.templates.map((template) => (
                          <option key={template.id} value={template.id}>
                            {template.name} · {template.version} ·{" "}
                            {template.spec.cpu} 核 /{" "}
                            {template.spec.memory_mb / 1024} GB
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <div className="workspace-actions">
                    {!ws ? (
                      <button
                        className="button primary"
                        disabled={busy || !catalog?.templates.length}
                        onClick={() =>
                          run(async () => {
                            const created = await api("/workspaces", "POST", {
                              name: `${user.display_name}的工作空间`,
                              template_id:
                                selectedTemplate || catalog?.templates[0]?.id,
                            });
                            setWs(created);
                            await refresh();
                            notify(
                              "空间已开通，可以直接启动，模型连接可稍后配置",
                            );
                          })
                        }
                      >
                        <Plus size={18} />
                        开通我的工作空间
                      </button>
                    ) : ws.status === "running" ? (
                      <a
                        className="button primary"
                        href={ws.entry_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        进入 DeepSeek
                        <ArrowUpRight size={18} />
                      </a>
                    ) : ["starting", "stopping", "recovering"].includes(
                        ws.status,
                      ) ? (
                      <button className="button primary" disabled>
                        <Spinner />
                        {statusNames[ws.status]}
                      </button>
                    ) : (
                      <button
                        className="button primary"
                        disabled={busy}
                        onClick={() => action("start")}
                      >
                        <Play size={17} />
                        启动工作空间
                      </button>
                    )}
                    {ws?.desktop_url && (
                      <a
                        className="button secondary"
                        href={ws.desktop_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <Monitor size={16} />
                        浏览器桌面
                      </a>
                    )}
                    {ws && ["running", "recovering"].includes(ws.status) && (
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={() => action("stop")}
                      >
                        <Square size={15} />
                        停止空间
                      </button>
                    )}
                    {ws && (
                      <a href="#settings" className="text-link">
                        空间设置
                        <ArrowRight size={15} />
                      </a>
                    )}
                    {ws && !["starting", "stopping"].includes(ws.status) && (
                      <details className="more-actions">
                        <summary aria-label="更多空间操作">
                          <MoreHorizontal size={20} />
                        </summary>
                        <div>
                          <button
                            onClick={() => action("restart")}
                            disabled={busy}
                          >
                            重启空间
                          </button>
                          <button
                            onClick={() => action("rebuild")}
                            disabled={busy}
                          >
                            重建运行环境
                          </button>
                        </div>
                      </details>
                    )}
                  </div>
                </div>
                <div className="workspace-footer">
                  <span>
                    <HardDrive size={15} />
                    持久化项目与会话
                  </span>
                  <span>
                    <Layers3 size={15} />
                    独立运行环境
                  </span>
                  <span>
                    <Zap size={15} />
                    NEW API 模型接入
                  </span>
                </div>
              </div>
              {ws &&
                (!ws.has_api_token ||
                  !catalog?.gateway_configured ||
                  !catalog.models.includes(ws.model)) && (
                  <div className="notice notice-warning">
                    模型连接尚未配置完整，你仍可启动并进入空间。使用团队模型前，请在
                    <a href="#settings">空间设置</a>中补充配置并重启空间。
                  </div>
                )}
              {ws && ws.config_version !== ws.applied_version && (
                <div className="pending-config">
                  <Settings2 size={17} />
                  <span>有配置等待应用。下次启动或重启后生效。</span>
                  <a href="#settings">
                    查看设置
                    <ArrowRight size={14} />
                  </a>
                </div>
              )}
              <div className="resource-grid">
                <Resource
                  icon={<Cpu />}
                  label="计算资源"
                  value={`${ws?.runtime?.resources.cpu ?? user.cpu}`}
                  unit="vCPU"
                  description="运行时 CPU 上限"
                />
                <Resource
                  icon={<MemoryStick />}
                  label="运行内存"
                  value={`${(ws?.runtime?.resources.memory_mb ?? user.memory_mb) / 1024}`}
                  unit="GB"
                  description="运行时内存上限"
                />
                <Resource
                  icon={<HardDrive />}
                  label="存储提醒"
                  value={`${user.storage_mb / 1024}`}
                  unit="GB"
                  description="用量告警阈值 · 非硬配额"
                />
              </div>
              <div className="content-grid">
                <section className="panel">
                  <div className="panel-heading">
                    <h3>
                      <Folder size={18} />
                      项目文件
                    </h3>
                    <small>上传资料，取回成果</small>
                  </div>
                  {ws ? (
                    <Files workspace={ws} notify={notify} />
                  ) : (
                    <Empty>
                      <Folder size={30} />
                      <p>开通空间后，即可上传任务材料。</p>
                    </Empty>
                  )}
                </section>
                <section className="panel activity-panel">
                  <div className="panel-heading">
                    <h3>
                      <Clock3 size={18} />
                      最近活动
                    </h3>
                    <span className="pill">{ops.length}</span>
                  </div>
                  {ops.length ? (
                    <div className="activities">
                      {ops.slice(0, 6).map((op) => (
                        <div className="activity" key={op.id}>
                          <span className={`activity-dot ${op.status}`}>
                            {op.status === "succeeded" ? (
                              <Check size={12} />
                            ) : op.status === "failed" ? (
                              <X size={12} />
                            ) : (
                              <Clock3 size={12} />
                            )}
                          </span>
                          <div>
                            <strong>{actionNames[op.action]}</strong>
                            <p>{op.message}</p>
                            <time>{date(op.created_at)}</time>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty>
                      <Clock3 size={28} />
                      <p>空间操作会记录在这里。</p>
                    </Empty>
                  )}
                </section>
              </div>
              <div className="help-line">
                <CircleHelp size={16} />
                第一次使用？进入 Harness 后，选择 <code>/workspace</code>{" "}
                作为项目目录。
              </div>
            </>
          )}
          {page === "settings" && (
            <Settings
              workspace={ws}
              catalog={catalog}
              notify={notify}
              refresh={refresh}
              action={action}
            />
          )}
          {page === "admin" && user.role === "admin" && (
            <Admin notify={notify} />
          )}
          {!["workspace", "settings", "admin"].includes(page) && (
            <Empty>
              <p>页面不存在。</p>
              <a href="#workspace">返回工作空间</a>
            </Empty>
          )}
        </main>
        <footer className="page-footer">
          <span>DeepSeek 团队工作台</span>
          <span>持久化空间 · 按需运行</span>
        </footer>
      </div>
    </div>
  );
}

function Resource({
  icon,
  label,
  value,
  unit,
  description,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  description: string;
}) {
  return (
    <div className="resource">
      <div className="resource-label">
        {icon}
        {label}
      </div>
      <div className="resource-value">
        {value}
        <span>{unit}</span>
      </div>
      <small>{description}</small>
    </div>
  );
}

function Login({ onLogin }: { onLogin: (value: any) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="login-screen">
      <div className="login-appearance">
        <ThemeSelect />
      </div>
      <div className="login-story">
        <a className="brand" href="#">
          <span className="brand-icon">
            <Waves />
          </span>
          <span>
            DeepSeek<small>团队工作台</small>
          </span>
        </a>
        <div className="login-message">
          <span className="eyebrow">A SPACE TO DO YOUR BEST WORK</span>
          <h1>
            从一个想法，
            <br />
            到一份成果。
          </h1>
          <p>
            你的 Agent、项目文件与执行环境，
            <br />
            在同一个工作空间里准备就绪。
          </p>
          <div className="login-diagram">
            <span>
              <Terminal />
              任务
            </span>
            <i />
            <span className="active">
              <Waves />
              DeepSeek
            </span>
            <i />
            <span>
              <File />
              成果
            </span>
          </div>
        </div>
        <p className="login-bottom">
          <ShieldCheck size={17} />
          团队专属 · 数据持久保存
        </p>
      </div>
      <div className="login-form-wrap">
        <form
          className="login-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            const data = new FormData(e.currentTarget);
            try {
              onLogin(
                await api("/auth/login", "POST", Object.fromEntries(data)),
              );
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="eyebrow">WELCOME BACK</div>
          <h2>登录团队工作台</h2>
          <p>使用管理员为你创建的账号。</p>
          {error && (
            <div role="alert" className="notice notice-error">
              {error}
            </div>
          )}
          <label>
            用户名
            <input
              name="username"
              autoComplete="username"
              placeholder="你的团队账号"
              required
            />
          </label>
          <label>
            密码
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              placeholder="输入密码"
              required
            />
          </label>
          <button className="button primary login-submit" disabled={busy}>
            {busy ? (
              <Spinner />
            ) : (
              <>
                登录
                <ArrowRight size={17} />
              </>
            )}
          </button>
          <div className="login-tip">还没有账号？请联系团队管理员。</div>
        </form>
      </div>
    </div>
  );
}

function Files({
  workspace,
  notify,
}: {
  workspace: Workspace;
  notify: (text: string, error?: boolean) => void;
}) {
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<
    { name: string; is_dir: boolean; size: number }[]
  >([]);
  const [used, setUsed] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const load = useCallback(async () => {
    const data = await api(
      `/workspaces/${workspace.id}/files?path=${encodeURIComponent(path)}`,
    );
    setEntries(data.entries);
    setTruncated(data.truncated);
    api(`/workspaces/${workspace.id}/storage`)
      .then((r) => setUsed(r.used_bytes))
      .catch(() => {});
  }, [workspace.id, path]);
  useEffect(() => {
    load().catch((e) => notify(e.message, true));
  }, [load, notify]);
  return (
    <>
      <div className="file-toolbar">
        <button className="path-button" onClick={() => setPath("")}>
          项目根目录
        </button>
        {path && (
          <>
            <ChevronRight size={13} />
            <button
              className="path-button"
              onClick={() => setPath(path.split("/").slice(0, -1).join("/"))}
            >
              {path} ↥
            </button>
          </>
        )}
        <div className="spacer" />
        <button
          className="icon-button"
          aria-label="刷新文件"
          onClick={() => load().catch((e) => notify(e.message, true))}
        >
          <RefreshCw size={15} />
        </button>
        <label
          className={`button small secondary upload-label ${busy ? "disabled" : ""}`}
        >
          <Upload size={14} />
          上传
          <input
            type="file"
            disabled={busy}
            aria-label="上传文件"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              setBusy(true);
              const body = new FormData();
              body.append("file", file);
              try {
                await api(
                  `/workspaces/${workspace.id}/files?path=${encodeURIComponent(path)}`,
                  "POST",
                  body,
                );
                await load();
                notify("文件已上传");
              } catch (e) {
                notify((e as Error).message, true);
              } finally {
                setBusy(false);
                e.target.value = "";
              }
            }}
          />
        </label>
      </div>
      {entries.length ? (
        <div className="file-list">
          {entries.map((entry) => (
            <div className="file-row" key={entry.name}>
              {entry.is_dir ? (
                <Folder size={18} className="folder-icon" />
              ) : (
                <File size={18} />
              )}
              <span className="filename">
                {entry.is_dir ? (
                  <button
                    onClick={() =>
                      setPath([path, entry.name].filter(Boolean).join("/"))
                    }
                  >
                    {entry.name}
                  </button>
                ) : (
                  entry.name
                )}
              </span>
              <small>{entry.is_dir ? "文件夹" : formatBytes(entry.size)}</small>
              {!entry.is_dir && (
                <a
                  className="icon-button"
                  aria-label={`下载 ${entry.name}`}
                  href={`${BASE}/api/workspaces/${workspace.id}/download?path=${encodeURIComponent([path, entry.name].filter(Boolean).join("/"))}`}
                >
                  <ArrowDownToLine size={16} />
                </a>
              )}
            </div>
          ))}
        </div>
      ) : (
        <Empty>
          <Upload size={28} />
          <p>上传一份资料，开始你的第一个任务。</p>
          <small>单个文件最多 32 MB，同名文件不会被覆盖。</small>
        </Empty>
      )}
      <div className="file-footer">
        <span>
          {entries.length} 个项目{truncated && " · 仅显示前 1000 项"}
        </span>
        <span>空间已用 {used === null ? "—" : formatBytes(used)}</span>
      </div>
    </>
  );
}

function Settings({
  workspace: ws,
  catalog,
  notify,
  refresh,
  action,
}: {
  workspace: Workspace | null;
  catalog: Catalog | null;
  notify: (text: string, error?: boolean) => void;
  refresh: () => Promise<void>;
  action: (name: string) => void;
}) {
  const [draft, setDraft] = useState({
    model: "",
    api_token: "",
    language: "中文",
    instructions: "",
    plugins: [] as string[],
    expected_version: 0,
  });
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (ws && !dirty)
      setDraft({
        model: ws.model,
        api_token: "",
        language: ws.language,
        instructions: ws.instructions,
        plugins: ws.plugins,
        expected_version: ws.config_version,
      });
  }, [ws, dirty]);
  const edit = (value: Partial<typeof draft>) => {
    setDraft((d) => ({ ...d, ...value }));
    setDirty(true);
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">MAKE IT YOURS</div>
          <h1>空间设置</h1>
          <p>设置模型、工作偏好和预置能力。</p>
        </div>
        {ws && (
          <span className="revision">
            配置 v{ws.config_version}
            {ws.config_version === ws.applied_version
              ? " · 已应用"
              : " · 待应用"}
          </span>
        )}
      </div>
      {!ws ? (
        <section className="panel">
          <Empty>
            <Settings2 size={30} />
            <p>请先开通工作空间。</p>
            <a href="#workspace" className="button primary">
              前往开通
              <ArrowRight size={16} />
            </a>
          </Empty>
        </section>
      ) : (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              await api(`/workspaces/${ws.id}/config`, "PUT", {
                ...draft,
                api_token: draft.api_token || null,
              });
              setDirty(false);
              await refresh();
              notify("设置已保存，下次启动或重启后应用");
            } catch (e) {
              notify((e as Error).message, true);
            } finally {
              setBusy(false);
            }
          }}
        >
          <WorkspaceEnvironment
            workspace={ws}
            templates={catalog?.templates || []}
            request={api}
            refresh={refresh}
            notify={notify}
            disabled={busy || dirty}
          />
          <section className="panel settings-panel">
            <div className="section-intro">
              <span className="section-icon">
                <Zap />
              </span>
              <div>
                <h3>模型连接</h3>
                <p>使用团队模型时配置；留空也可以启动和进入空间。</p>
              </div>
            </div>
            {!catalog?.gateway_configured && (
              <div className="notice notice-warning">
                管理员尚未配置 NEW API
                网关。你可以先进入空间，稍后再配置模型连接。
              </div>
            )}
            <div className="form-grid">
              <label>
                默认模型
                <select
                  value={draft.model}
                  onChange={(e) => edit({ model: e.target.value })}
                >
                  <option value="">稍后配置</option>
                  {catalog?.models.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
              </label>
              <label>
                模型调用令牌
                <span className="input-icon">
                  <KeyRound size={16} />
                  <input
                    type="password"
                    autoComplete="off"
                    value={draft.api_token}
                    onChange={(e) => edit({ api_token: e.target.value })}
                    placeholder={
                      ws.has_api_token
                        ? "已保存 · 留空保留现有令牌"
                        : "输入 NEW API 模型令牌"
                    }
                  />
                </span>
                <small>只保存加密凭证，保存后不再回显。</small>
              </label>
            </div>
            <button
              className="button secondary small"
              type="button"
              disabled={busy || dirty || !ws.has_api_token}
              onClick={async () => {
                setBusy(true);
                try {
                  const r = await api(
                    `/workspaces/${ws.id}/test-model`,
                    "POST",
                  );
                  notify(r.message, !r.ok);
                } catch (e) {
                  notify((e as Error).message, true);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <RefreshCw size={14} />
              检查已保存的连接
            </button>
          </section>
          <section className="panel settings-panel">
            <div className="section-intro">
              <span className="section-icon">
                <Settings2 />
              </span>
              <div>
                <h3>工作偏好</h3>
                <p>应用到空间中的个人指令文件，供 Agent 读取。</p>
              </div>
            </div>
            <label className="short-field">
              回复语言
              <select
                value={draft.language}
                onChange={(e) => edit({ language: e.target.value })}
              >
                <option>中文</option>
                <option>English</option>
              </select>
            </label>
            <label>
              个人指令
              <textarea
                rows={4}
                maxLength={8000}
                value={draft.instructions}
                onChange={(e) => edit({ instructions: e.target.value })}
                placeholder="例如：修改代码前先说明思路，完成后告诉我运行结果和文件位置。"
              />
              <small>{draft.instructions.length} / 8000</small>
            </label>
          </section>
          <section className="panel settings-panel">
            <div className="section-intro">
              <span className="section-icon">
                <Layers3 />
              </span>
              <div>
                <h3>预置插件</h3>
                <p>
                  可选插件变更后需要重启。浏览器桌面会额外启动
                  Chromium，登录状态随空间持久保存。
                </p>
              </div>
            </div>
            <div className="plugin-list">
              {catalog?.plugins.map((plugin) => (
                <div className="plugin-row" key={plugin.id}>
                  <div>
                    <strong>{plugin.name}</strong>
                    <p>{plugin.description}</p>
                  </div>
                  {plugin.editable ? (
                    <label className="toggle">
                      <input
                        type="checkbox"
                        aria-label={plugin.name}
                        checked={draft.plugins.includes(plugin.id)}
                        onChange={(e) => {
                          const plugins = new Set(draft.plugins);
                          if (e.target.checked) {
                            plugins.add(plugin.id);
                            if (plugin.id === "browser-use")
                              plugins.add("browser-desktop");
                          } else {
                            plugins.delete(plugin.id);
                            if (plugin.id === "browser-desktop")
                              plugins.delete("browser-use");
                          }
                          edit({ plugins: [...plugins] });
                        }}
                      />
                      <span />
                    </label>
                  ) : (
                    <span className="pill">环境内置</span>
                  )}
                </div>
              ))}
            </div>
          </section>
          <div className="save-bar">
            <span>
              {dirty ? "有未保存的更改" : "配置变更在下次启动或重启后应用"}
            </span>
            {!dirty &&
              ws.status === "running" &&
              ws.config_version !== ws.applied_version && (
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => action("restart")}
                >
                  重启并应用
                </button>
              )}
            <button className="button primary" disabled={busy || !dirty}>
              {busy ? <Spinner /> : <Check size={16} />}保存设置
            </button>
          </div>
        </form>
      )}
      <PasswordForm notify={notify} />
    </>
  );
}

function PasswordForm({
  notify,
}: {
  notify: (text: string, error?: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <details className="password-panel">
      <summary>
        <KeyRound size={16} />
        修改登录密码
      </summary>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await api(
              "/auth/password",
              "PUT",
              Object.fromEntries(new FormData(e.currentTarget)),
            );
            window.dispatchEvent(new Event("session-expired"));
          } catch (e) {
            notify((e as Error).message, true);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-grid">
          <label>
            原密码
            <input
              type="password"
              name="old_password"
              required
              autoComplete="current-password"
            />
          </label>
          <label>
            新密码
            <input
              type="password"
              name="new_password"
              required
              minLength={12}
              autoComplete="new-password"
              placeholder="至少 12 个字符"
            />
          </label>
        </div>
        <button className="button secondary" disabled={busy}>
          修改密码并重新登录
        </button>
      </form>
    </details>
  );
}

function Admin({
  notify,
}: {
  notify: (text: string, error?: boolean) => void;
}) {
  const [tab, setTab] = useState("members");
  const [users, setUsers] = useState<User[]>([]);
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const [platform, setPlatform] = useState<any>(null);
  const [audit, setAudit] = useState<any[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [quotaUser, setQuotaUser] = useState<User | null>(null);
  const [templates, setTemplates] = useState<EnvironmentTemplate[]>([]);
  const [selectedSpaces, setSelectedSpaces] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const [u, w, p, a, t] = await Promise.all([
      api("/admin/users"),
      api("/admin/workspaces"),
      api("/admin/platform"),
      api("/admin/audit"),
      api("/admin/templates"),
    ]);
    setUsers(u);
    setSpaces(w);
    setPlatform(p);
    setAudit(a);
    setTemplates(t);
  }, []);
  useEffect(() => {
    load().catch((e) => notify(e.message, true));
  }, [load, notify]);
  const mutate = async (task: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try {
      await task();
      await load();
      notify(message);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">TEAM CONTROL</div>
          <h1>管理后台</h1>
          <p>管理成员、运行资源和团队默认环境。</p>
        </div>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => load().catch((e) => notify(e.message, true))}
        >
          <RefreshCw size={16} />
          刷新
        </button>
      </div>
      <div className="tabs">
        {[
          ["members", "团队成员"],
          ["instances", "运行空间"],
          ["templates", "运行模板"],
          ["platform", "环境与模型"],
          ["audit", "操作记录"],
        ].map(([id, text]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {text}
          </button>
        ))}
      </div>
      {tab === "templates" && (
        <TemplateManager
          templates={templates}
          request={api}
          reload={load}
          notify={notify}
        />
      )}
      {tab === "members" && (
        <section className="panel">
          <div className="panel-heading">
            <h3>
              团队成员 <span className="pill">{users.length}</span>
            </h3>
            <button
              className="button primary small"
              onClick={() => setShowCreate(true)}
            >
              <Plus size={15} />
              添加成员
            </button>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>成员</th>
                  <th>角色</th>
                  <th>资源上限</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <strong>{u.display_name}</strong>
                      <small className="subtext">{u.username}</small>
                    </td>
                    <td>{u.role === "admin" ? "管理员" : "成员"}</td>
                    <td>
                      {u.cpu} 核 / {u.memory_mb / 1024} GB
                    </td>
                    <td>
                      <span className={`pill ${u.active ? "good" : ""}`}>
                        {u.active ? "正常" : "已停用"}
                      </span>
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="text-link"
                          disabled={busy}
                          onClick={() => setQuotaUser(u)}
                        >
                          配额
                        </button>
                        {u.role !== "admin" && (
                          <button
                            className={`text-link ${u.active ? "danger-text" : ""}`}
                            disabled={busy}
                            onClick={() => {
                              if (
                                !u.active ||
                                confirm(
                                  `停用 ${u.display_name} 后，将撤销其登录并停止空间。是否继续？`,
                                )
                              )
                                mutate(
                                  () =>
                                    api(`/admin/users/${u.id}`, "PATCH", {
                                      active: !u.active,
                                    }),
                                  "成员状态已更新",
                                );
                            }}
                          >
                            {u.active ? "停用" : "启用"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {tab === "instances" && (
        <section className="panel">
          <div className="panel-heading">
            <h3>全部工作空间</h3>
            <small>管理员可停止运行，文件内容仅所有者可访问</small>
          </div>
          <div className="batch-toolbar">
            <label className="batch-select">
              <input
                className="row-select"
                type="checkbox"
                aria-label="选择全部空间"
                checked={
                  spaces.length > 0 && selectedSpaces.length === spaces.length
                }
                onChange={(event) =>
                  setSelectedSpaces(
                    event.target.checked ? spaces.map((w) => w.id) : [],
                  )
                }
              />
              已选择 {selectedSpaces.length} 个空间
            </label>
            {(["start", "stop", "restart"] as const).map((action) => (
              <button
                key={action}
                className="button secondary small"
                disabled={busy || !selectedSpaces.length}
                onClick={async () => {
                  if (
                    action !== "start" &&
                    !confirm("此操作会中断所选空间的运行任务，是否继续？")
                  )
                    return;
                  setBusy(true);
                  try {
                    const result = await api(
                      "/admin/workspaces/actions",
                      "POST",
                      { workspace_ids: selectedSpaces, action },
                    );
                    await load();
                    const failed = result.results.filter((r: any) => !r.ok);
                    notify(
                      `${result.results.length - failed.length} 个操作已提交${
                        failed.length
                          ? `，${failed.length} 个未提交：${failed
                              .map((r: any) => r.message)
                              .slice(0, 2)
                              .join("；")}`
                          : ""
                      }`,
                      failed.length > 0,
                    );
                  } catch (error) {
                    notify((error as Error).message, true);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                批量
                {action === "start"
                  ? "启动"
                  : action === "stop"
                    ? "停止"
                    : "重启"}
              </button>
            ))}
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>选择</th>
                  <th>工作空间</th>
                  <th>所有者</th>
                  <th>状态</th>
                  <th>配置</th>
                  <th>环境版本</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {spaces.map((w) => (
                  <tr key={w.id}>
                    <td>
                      <input
                        className="row-select"
                        type="checkbox"
                        aria-label={`选择 ${w.name}`}
                        checked={selectedSpaces.includes(w.id)}
                        onChange={(event) =>
                          setSelectedSpaces((ids) =>
                            event.target.checked
                              ? [...ids, w.id]
                              : ids.filter((id) => id !== w.id),
                          )
                        }
                      />
                    </td>
                    <td>
                      <strong>{w.name}</strong>
                    </td>
                    <td>
                      {users.find((u) => u.id === w.user_id)?.display_name}
                    </td>
                    <td>
                      <State value={w.status} />
                    </td>
                    <td>
                      v{w.applied_version} / v{w.config_version}
                    </td>
                    <td>
                      <strong>
                        {w.runtime?.template_name} ·{" "}
                        {w.runtime?.template_version}
                      </strong>
                      <small className="subtext">
                        {w.runtime?.actual_image_id
                          ? w.runtime.actual_image_id.slice(0, 19)
                          : "未运行"}
                      </small>
                    </td>
                    <td>
                      <select
                        className="upgrade-select"
                        aria-label={`升级 ${w.name}`}
                        value={w.runtime?.template_id || ""}
                        disabled={busy}
                        onChange={(event) => {
                          const templateId = event.target.value;
                          if (
                            confirm(
                              "升级会重建此空间的运行容器，并保留原项目和会话。是否继续？",
                            )
                          )
                            mutate(
                              () =>
                                api(
                                  `/admin/workspaces/${w.id}/upgrade`,
                                  "POST",
                                  {
                                    template_id: templateId,
                                    expected_version: w.config_version,
                                  },
                                ),
                              "环境升级已提交",
                            );
                        }}
                      >
                        {w.runtime &&
                          !templates.some(
                            (t) => t.id === w.runtime?.template_id && t.enabled,
                          ) && (
                            <option value={w.runtime.template_id}>
                              {w.runtime.template_name}（已停用）
                            </option>
                          )}
                        {templates
                          .filter(
                            (t) =>
                              t.published &&
                              t.enabled &&
                              (!w.runtime ||
                                t.spec.compatibility_group ===
                                  w.runtime.compatibility_group),
                          )
                          .map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.name} · {t.version}
                            </option>
                          ))}
                      </select>
                      <button
                        className="text-link danger-text"
                        disabled={busy || w.status === "stopped"}
                        onClick={() => {
                          if (
                            confirm(
                              "停止此空间会中断正在执行的任务，是否继续？",
                            )
                          )
                            mutate(
                              () =>
                                api(`/admin/workspaces/${w.id}/stop`, "POST"),
                              "已请求停止空间",
                            );
                        }}
                      >
                        停止空间
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!spaces.length && <Empty>暂无工作空间。</Empty>}
          </div>
        </section>
      )}
      {tab === "platform" && platform && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const data = new FormData(e.currentTarget);
            mutate(
              () =>
                api("/admin/platform", "PUT", {
                  gateway_url: data.get("gateway_url"),
                  models: String(data.get("models")).split("\n"),
                  template_name: platform.template_name,
                }),
              "团队配置已保存，现有实例需重启应用",
            );
          }}
        >
          <section className="panel settings-panel">
            <div className="section-intro">
              <span className="section-icon">
                <Zap />
              </span>
              <div>
                <h3>NEW API 网关</h3>
                <p>配置现有服务。模型请求从成员的运行环境直接发出。</p>
              </div>
            </div>
            <label>
              API 基础地址
              <input
                name="gateway_url"
                type="url"
                defaultValue={platform.gateway_url}
                placeholder="https://your-new-api.example/v1"
              />
              <small>
                填写兼容接口的完整基础地址，通常以 /v1
                结尾。容器需能访问该地址。
              </small>
            </label>
            <label>
              可选模型
              <textarea
                name="models"
                rows={4}
                defaultValue={platform.models.join("\n")}
                placeholder={"每行一个网关中的模型 ID"}
              />
              <small>
                成员只能在 Launcher 中选择这里列出的模型。实际调用权限由 NEW API
                令牌控制。
              </small>
            </label>
          </section>
          <section className="panel settings-panel">
            <div className="section-intro">
              <span className="section-icon">
                <Box />
              </span>
              <div>
                <h3>平台运行设置</h3>
                <p>
                  新空间从已发布的运行模板中选择环境，镜像和版本随空间保存。
                </p>
              </div>
            </div>
            <div className="environment-info">
              <span>运行底座</span>
              <code>{platform.runtime_image.split("@")[0]}</code>
              <span>同时运行上限</span>
              <strong>{platform.max_running} 个空间</strong>
              <span>底座来源</span>
              <a href={platform.base_source} target="_blank" rel="noreferrer">
                runzhliu/deepseek-harness-docker <ArrowUpRight size={13} />
              </a>
            </div>
          </section>
          <div className="save-bar">
            <span>地址与模型变更将在实例重启后生效</span>
            <button className="button primary" disabled={busy}>
              {busy ? <Spinner /> : <Check size={16} />}保存团队配置
            </button>
          </div>
        </form>
      )}
      {tab === "audit" && (
        <section className="panel">
          <div className="panel-heading">
            <h3>最近 100 条管理与使用记录</h3>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>操作者</th>
                  <th>动作</th>
                  <th>对象 ID</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((event) => (
                  <tr key={event.id}>
                    <td>{date(event.created_at)}</td>
                    <td>
                      {users.find((u) => u.id === event.actor_id)
                        ?.display_name || "—"}
                    </td>
                    <td>
                      <code>{event.action}</code>
                    </td>
                    <td>
                      <code>{event.target_id.slice(0, 12)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {showCreate && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-member-title"
          >
            <div className="panel-heading">
              <h3 id="create-member-title">添加团队成员</h3>
              <button
                className="icon-button"
                aria-label="关闭"
                onClick={() => setShowCreate(false)}
              >
                <X size={19} />
              </button>
            </div>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const data = Object.fromEntries(new FormData(e.currentTarget));
                setBusy(true);
                try {
                  await api("/admin/users", "POST", data);
                  await load();
                  setShowCreate(false);
                  notify("成员已创建，请将初始密码交给该成员");
                } catch (e) {
                  notify((e as Error).message, true);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label>
                用户名
                <input
                  name="username"
                  autoFocus
                  required
                  pattern="[a-zA-Z0-9_.-]{3,40}"
                  placeholder="3–40 位字母、数字或 ._-"
                />
              </label>
              <label>
                显示名称
                <input name="display_name" required maxLength={60} />
              </label>
              <label>
                初始密码
                <input
                  name="password"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  placeholder="至少 12 个字符"
                />
              </label>
              <p className="muted">
                默认分配 2 vCPU、2 GB 内存，存储告警阈值 10 GB。
              </p>
              <button className="button primary" disabled={busy}>
                创建成员
                <ArrowRight size={16} />
              </button>
            </form>
          </section>
        </div>
      )}
      {quotaUser && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="quota-title"
          >
            <div className="panel-heading">
              <h3 id="quota-title">{quotaUser.display_name} · 资源配额</h3>
              <button
                className="icon-button"
                aria-label="关闭"
                onClick={() => setQuotaUser(null)}
              >
                <X size={19} />
              </button>
            </div>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const d = Object.fromEntries(new FormData(e.currentTarget));
                await mutate(
                  () =>
                    api(`/admin/users/${quotaUser.id}`, "PATCH", {
                      cpu: Number(d.cpu),
                      memory_mb: Number(d.memory_mb),
                      storage_mb: Number(d.storage_mb),
                    }),
                  "资源配额已保存，重启空间后应用",
                );
                setQuotaUser(null);
              }}
            >
              <label>
                CPU（核）
                <input
                  name="cpu"
                  type="number"
                  min={0.5}
                  max={16}
                  step={0.5}
                  defaultValue={quotaUser.cpu}
                  required
                />
              </label>
              <label>
                内存（MB）
                <input
                  name="memory_mb"
                  type="number"
                  min={512}
                  max={32768}
                  step={512}
                  defaultValue={quotaUser.memory_mb}
                  required
                />
              </label>
              <label>
                存储告警阈值（MB）
                <input
                  name="storage_mb"
                  type="number"
                  min={128}
                  max={1048576}
                  defaultValue={quotaUser.storage_mb}
                  required
                />
              </label>
              <button className="button primary" disabled={busy}>
                保存配额
              </button>
            </form>
          </section>
        </div>
      )}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
