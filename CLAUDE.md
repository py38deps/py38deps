## 项目目标

随着低版本 python 逐渐 end of life，许多 python 依赖逐渐提高了最低版本要求，`py38deps` 将他们反向移植到低版本 python 上，尽量支持到 python 3.8。

## 文件结构

`README.md` 表格中记录了已经移植完成的依赖

`repo/{DEP_NAME}` 是各个依赖的 git 仓库。remote origin 指向我们的二次开发仓库，remote upstream 指向官方仓库

`wheel/{DEP_NAME}/{DEP_VERSION}` 目录存放编译好的 wheel 文件

## 对话要求

- 在对话回复中使用中文
- 在代码注释中使用英文
- 在修改已有代码的时候，不要对无关的部分进行修改

##  测试环境

本地有 cp38 ~ cp314 的测试环境，python 运行时提取自 uv 打包好的 portable python

```
envs/cp38/python.exe
envs/cp39/python.exe
envs/cp310/python.exe
envs/cp311/python.exe
envs/cp312/python.exe
envs/cp313/python.exe
envs/cp313t/python.exe
envs/cp314/python.exe
envs/cp314t/python.exe
```

### 编译工具

编译工具在 `mingw64/bin` 目录下，禁止自行在系统环境中安装编译工具。

### 优先使用codewhale内置工具

优先使用 codewhale 内置工具而不是调用外部命令行。如果遇到权限问题再去尝试使用命令行访问。如果在任务中多次需要访问一个外部路径，提示用户可以执行 `/trust add {PATH}` 添加信任，这样下一次可以使用内置工具进行访问。

内置工具是使用 rust 编写的，比命令行更快。内置工具会过滤类似 `node_modules`，`build` 等目录，避免搜索无关内容消耗大量时间。

- 在查找文件的时候优先使用 `file_search` 工具或者 `list_dir` 工具来查找文件，而不是使用 `find` 命令或者 `Get-ChildItem` 命令。

- 在查找文件内容优先使用 `grep_files` 工具来查找，而不是使用 `grep` 命令或者 `Select-String` 命令。
- 优先使用内置的 git 工具来查看仓库状态，而不是执行 git 命令。
- 在批量编辑文件的时候优先使用内置的 `edit` / `fim_edit` / `apply_patch` 工具进行编辑，而不是编写临时脚本去执行替换。因为内置工具有丰富的模糊匹配与约束，自行编写替换代码很容易遇到 文件编码问题、换行符匹配问题、行号/内容匹配问题、特殊符号转义问题。不要怕多次调用工具进行单次编辑很麻烦，遇到编码问题转义问题反复修改临时脚本更麻烦。

### 运行python程序

使用测试环境中的 python 解释器来执行脚本和运行测试，比如：

```bash
# run a specific file "module/config/gen.py"
"<path_to_python>" -m module.config.gen
# run test file "tests/base/test_servertime.py"
"<path_to_python>" -m pytest tests/base/test_servertime.py
```

不要直接使用 `python` 命令，因为项目有单独配好的虚拟环境，不使用全局 python 环境。

在运行项目内文件的时候不要直接执行 `python module/config/gen.py` 而是使用 `python -m module.config.gen` 作为模块运行，这样运行路径会在项目根目录。

### 编写计划文档

如果用户要求编写计划文档，那么将计划写入到 markdown 文件 `doc/{yyyy}-{mm}-{dd}_{title}.md`，比如 `doc/2026-08-13_somethong-matters.md`

### 编写 LIMITS 文档

`doc/LIMITS-{DEP_NAME}.md` 记录 backport 在**运行时会与官方仓库有差异的功能**（例如 `doc/LIMITS-truststore.md`）。编写原则：

- 只记录**运行时**行为差异：用户代码在低版本 Python 上观察到的、与官方仓库（在其支持的版本上）不一致的行为，以及 fork 特有的行为改动。
- **不记录**测试内容（测试跳过、fixture、测试语法改写等）和 typehint 内容（PEP 604 union、TypeAlias、`from __future__ import annotations`、typing_extensions 等）。
- **正确适配不记录**：如果 upstream 源码本身就有版本条件分支（如 `sys.version_info >= (3, 11)` gate、内嵌的 `asyncio.Runner`、`exceptiongroup` 依赖标记、`anext` shim 等），backport 只是把支持范围扩展到 3.8/3.9 且用户可见行为与官方一致，这属于正确适配，不要写进 LIMITS 文档。
- 记录前必须核实差异真实存在：用 `git show <upstream_tag>:<file>` 对比 upstream 与 backport 源码，确认改动是 fork 特有的且影响运行时行为。常见可记录项：
  - 低版本 API 缺失导致参数被静默忽略（如 `subprocess.Popen` 的 user/group/umask、`Path.write_text()` 的 newline）
  - fork 特有的行为改动（如 daemon 线程、错误容忍逻辑）
  - 依赖版本 pin 与 upstream 不同（如按 Python 版本 pin trio）
  - `requires-python` 差异与 import gate 的移除
- 结构建议：核心限制（为什么低版本做不到）→ 各平台/版本影响 → 其他差异 → 汇总表；用英文撰写，风格与 `doc/LIMITS-truststore.md` 一致。
- 文档命名：`doc/LIMITS-{DEP_NAME}.md`；README 表格的 LIMITS 列用 `[LIMITS](doc/LIMITS-{DEP_NAME}.md)` 链接，没有差异就留空。

### PowerShell兼容提示

Windows 运行环境下的 PowerShell 版本可能很低，执行这样的命令会报错，因为不支持 `&&`

```powershell
cd "E:\xxx" && "<path_to_python>" -m pytest ...
```

改用 `;` 分隔命令，并且增加 `&` 来表示调用被引号包裹的路径

```powershell
cd "E:\xxx"; & "<path_to_python>" -m pytest ...
```

因为 codewhale （也包括其他 AI agent）执行命令时会创建新的命令行环境，所以回退或者不回退路径都无所谓。

### 临时运行python代码提示

如果你希望临时运行一段简单的 python 测试代码，不要使用 `python -c` 去运行，因为编写转义非常容易出错，使用 stdin 去输入代码。

在 Windows 上这样运行：

注意，即便运行的代码只有一行也必须写成多行，因为 `@'` 和 `'@` 标记需要在行开头

```powershell
cd "E:\xxx";
@'
print("hello world")
import json
data = {"key": "value"}
print(json.dumps(data))
'@ | & "<path_to_python>"
```

在 Linux 上这样运行，通过 heredoc 传入：

注意，必须给 heredoc 定界符加引号（`<< 'EOF'`），否则 `$` 和反引号会被 shell 展开。

```bash
cd /path/to/dir
python << 'EOF'
print("hello world")
import json
data = {"key": "value"}
print(json.dumps(data))
EOF
```

如果需要临时执行的代码过于复杂，或者需要 stdin，那么在项目根目录编写临时文件来运行它。

### 临时文件要求

如果需要下载临时文件，下载到项目根目录的 tmp 文件夹，不要下载到系统 temp 目录

### 禁止事项

1. 禁止使用系统环境中的 git 凭证访问 github 中受限的内容（例如 github actions logs，github actions artifacts 等），必须使用 mcp 工具操作受限内容。访问非受限内容（比如公开仓库的文件）无需携带凭证，不在禁止范围内。

   这将避免用户凭证泄漏到上下文中。如果 MCP 工具未配置/无法使用/无法访问，立刻上报用户。

2. 禁止读取 mcp 配置文件，提取其中的 GITHUB_TOKEN 来访问受限内容，原因同上。

3. `git push` 必须征得用户同意，禁止自动 `git push`

4. 禁止将本地构建的 wheel 放入 `wheel` 文件夹，只能存放来自 CI 的构建。


## 1. 反向移植流程流程概述

### 1.2 本地修改与测试

1. 回退 git 历史到最新版本的 tag，因为我们需要构建的是最新版本的反向移植，不应该引入未发布的内容

2. 查找 git 历史，看看是哪些 commit 移除了旧版本的支持，回退这些修改。

3. 找到废除旧版本支持后的变更，查看变更是否引入了旧版本不支持的python语法。
4. 在 cp38 下进行测试，确认基本行为正确，并且没有不支持的语法
5. 测试通过了之后再在其余 python 版本下运行完整测试。
6. 所有本地测试通过后，使用 `git commit` 提交代码，请求用户 review 代码，进入第 1.2 章节 CI 构建。

> 对于纯python实现的库，可以直接使用测试环境的python运行时进行测试，不需要把库安装到环境中。
>
> 对于需要编译的库，才安装到环境中。

### 1.2 CI构建

1. 请求用户 review 代码，如果用户表示通过或者要求进行推送，执行 `git push` 推送代码，这将自动触发 CI

2. 进入第 4 章节，轮询 Github Actions 运行状态

3. 如果发现错误，停止轮询并开始进行本地修复，不等待整个 action 运行完成。进入第 5 章节，拉取 log 然后进行修复。本地修复完成后继续轮询当前 action，发现新的错误继续进行本地修复，直到整个 action 运行完成，收集到所有错误和修复所有错误。

   全部修复完成后，回到第 1.2 章节开头，重新请求用户 review 代码，禁止在修复后直接 `git push`，禁止在上一个 action 未完成前，通过 `git push` 触发新的 action

4. 如果 action 运行全部成功，进入第 6 章节，下载 artifacts 放入 wheel 文件夹。

### 1.3 更新主仓库 py38deps

1. 请求用户检查当前状态，通过则进行下一步。子仓库未完成迁移，不能更新主仓库
2. 进入第 7 章节更新主仓库

## 2. 创建移植库流程

在创建新的移植库之前，需要知道：

- 二次开发仓库的地址（`ORIGIN_URL`），例如：`git@github.com:LmeSzinc/python-zstandard.git` ，二次开发地址需要使用 git 协议，如果是 HTTP 地址需要转换为 git 协议地址。
- 官方仓库地址（`UPSTREAM_URL`），例如：`https://github.com/indygreg/python-zstandard`，使用 HTTP 地址
- 依赖名称（`DEP_NAME`），例如 `python-zstandard`。注意，这里使用的是库名称（Distribution Name），也就是在 Pypi 中注册的名字，用于 `pip install ...` ；而不是导入名称（Import Name）。对于 `python-zstandard` 而言，库名称叫 `python-zstandard`，而导入名称是 `zstandard`。

### 2.1 添加submodule

```bash
git submodule add <ORIGIN_URL> repo/<DEP_NAME>
```

### 2.2 配置子模块的 Remote (Origin 与 Upstream)

进入子模块内部，配置远程仓库关联：

```bash
cd repo/<DEP_NAME>

# 确认 origin 指向二次开发仓库
git remote set-url origin <ORIGIN_URL>

# 添加 upstream 指向官方仓库
git remote add upstream <UPSTREAM_URL>

# 拉取 upstream 的所有分支与 tag 信息
git fetch upstream --tags
```

### 2.3 切换到二次开发分支 (防止游离态/Detached HEAD)

如果你直接进入子模块目录修改代码并 git commit，代码可能会提交到一个“游离分支”，导致丢代码。**必须**显式切换到二次开发分支，严禁在临时提交上工作。

`BACKPORT_BRANCH` 一般是 `main` 分支或者 `master` 分支

```bash
# 从 upstream 的默认分支（或指定 tag）切出新的 backport 分支
# 注意：如果 origin 远程已经存在该分支，则直接 checkout
git checkout <BACKPORT_BRANCH>
```

## 3. 可用工具（会话内调用名 `mcp_github--actions_*`，注意双下划线）

| 工具                  | 方法                                                         | 用途                                                         |
| --------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `actions_list`        | `list_workflows` / `list_workflow_runs` / `list_workflow_jobs` / `list_workflow_run_artifacts` | 列 workflow / run / job / artifact（artifact 返回完整 JSON：**id、size_in_bytes、digest(sha256)**） |
| `actions_get`         | `get_workflow_run` / `get_workflow_job` / `get_workflow_run_logs_url` / `download_workflow_run_artifact` / `get_workflow_run_usage` | 详情 / 日志签名 URL / artifact 签名 URL                      |
| `get_job_logs`        | `job_id` 或 `run_id`+`failed_only`；`return_content`、`tail_lines` | **直接返回日志文本**（快速调试用）；`failed_only=true` 只看失败 job |
| `actions_run_trigger` | `run_workflow` / `rerun_workflow_run` / `rerun_failed_jobs` / `cancel_workflow_run` / `delete_workflow_run_logs` | 触发 / 重跑 / 取消                                           |

## 4. 轮询 GitHub Actions 流程

目标：push 触发 CI 后，以 **3 分钟**为间隔持续观察，**一旦发现错误立即停下轮询去修复**，不等待整个 run 跑完；修复后重新轮询，发现新的错误继续修复，直到整个 actions 运行完成。

不要用更短间隔（API 限速 60 次/小时，3 分钟足够及时）；如果错误出现后修复动作本身耗时较长，间隔不影响整体效率。

### 4.1 找最新触发的 run（含 head_sha 核对）

```jsonc
// 1. 列最近 runs（按时间倒序，第一条即最新）
mcp_github--actions_actions_list(owner, repo, method="list_workflow_runs", resource_id=<workflow_id 可选>, workflow_runs_filter={"branch": "main"})

// 2. 核对 head_sha == 本地 commit（官方 filter 不支持 head_sha，必须核对）
mcp_github--actions_actions_get(owner, repo, method="get_workflow_run", resource_id=<run_id>)
// 返回的 head_sha 与本地 git rev-parse HEAD 对比；不一致则取下一条 run
```

### 4.2 每次轮询检查什么

```jsonc
// 每个 job 的状态（关键：job 可能先于 run 失败）
mcp_github--actions_actions_list(owner, repo, method="list_workflow_jobs", resource_id=<run_id>)
```

判断逻辑：

1. 任一 job `conclusion: failure`（run 可能还在 `in_progress`，其他 job 可能还在跑）→ **立即停止轮询，进入修复**，不等待其他 job 跑完
2. 全部 job `conclusion: success` → 进入下载 artifact

## 5. 拉取 log 流程（下载到 tmp 缓存）

### 5.1 找到目标 run

```jsonc
mcp_github--actions_actions_list(owner, repo, method="list_workflow_runs", resource_id=<workflow_id 可选>, workflow_runs_filter={...})
// 返回每个 run 的 id / run_number / name / status / conclusion / head_branch
```

### 5.2 拿日志签名 URL 并下载

```jsonc
mcp_github--actions_actions_get(owner, repo, method="get_workflow_run_logs_url", resource_id=<run_id>)
// 返回 logs_url（results-receiver.actions.githubusercontent.com 带签名，匿名可下，有时效，尽快下载）
```

> 下载 GitHub 日志 / 产物需通过代理（clash 等），在 curl 中加 `--proxy http://127.0.0.1:7890`：

```powershell
cd "E:\ProgramData\Pycharm\py38deps"
New-Item -ItemType Directory -Force -Path "tmp\msgspec" | Out-Null
curl.exe -sL --proxy http://127.0.0.1:7890 -o "tmp\msgspec\logs_27066034896.zip" "<logs_url>"
```

### 5.3 解压到同名文件夹（用 Python zipfile，不用 Windows tar.exe）

```powershell
@'
import zipfile
src = r"tmp\msgspec\logs_27066034896.zip"
dst = r"tmp\msgspec\logs_27066034896"
z = zipfile.ZipFile(src)
assert z.testzip() is None, "zip corrupted"   # 完整性校验
z.extractall(dst)
print(dst)
'@ | & "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe"
```

解压后结构：`tmp/{repo}/logs_{id}/` 内每个 job 一个 `<序号>_<job名>.txt`，另有 `<job名>/system.txt` 记录 runner 环境信息。

### 5.4 读取日志（用内置工具，无编码问题）

**不要用 PowerShell 打印日志内容**（控制台 GBK 遇到 UTF-8 BOM 会报 `UnicodeEncodeError`）。用内置工具：

- `list_dir(path="tmp/{repo}/logs_{id}")` 定位文件
- `read(path="tmp/{repo}/logs_{id}/0_xxx.txt")` 读取完整日志
- `grep_files(pattern="error|failed", path="tmp/{repo}/logs_{id}")` 快速定位失败原因

## 6. 下载 artifact 流程（下载到 tmp + 校验）

### 6.1 列出 artifact 拿 id / size / digest

```jsonc
mcp_github--actions_actions_list(owner, repo, method="list_workflow_run_artifacts", resource_id=<run_id>)
// 返回 artifacts 数组：id、name、size_in_bytes、digest("sha256:...")、expired、expires_at
```

### 6.2 拿签名 URL

```jsonc
mcp_github--actions_actions_get(owner, repo, method="download_workflow_run_artifact", resource_id=<artifact_id>)
// 返回 download_url（Azure Blob 带签名，匿名可下，有时效，尽快下载）
```

### 6.3 下载到 `tmp/{repo}/artifact_{name}.zip`

```powershell
cd "E:\ProgramData\Pycharm\py38deps"
curl.exe -sL --proxy http://127.0.0.1:7890 -o "tmp\msgspec\artifact_artifact-sdist.zip" "<download_url>"
```

### 6.4 校验（必须）并解压（先校验，通过后再解压）

下载完成后**必须先校验**（参考值来自 5.1 的返回），校验全部通过后才解压：

```powershell
# 第一步：校验（大小、sha256、zip 完整性）
@'
import hashlib, zipfile, os
path = r"tmp\msgspec\artifact_artifact-sdist.zip"
expected_size = 323824                 # size_in_bytes
expected_sha = "261e5e82..."           # digest 去掉 "sha256:" 前缀
size = os.path.getsize(path)
sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
assert size == expected_size, f"size mismatch: {size} != {expected_size}"
assert sha == expected_sha, f"sha256 mismatch: {sha}"
z = zipfile.ZipFile(path)
assert z.testzip() is None, "zip corrupted"
print("OK:", size, sha)
'@ | & "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe"

# 第二步：校验通过后再解压到同名文件夹，以msgspec为例
@'
import zipfile
z = zipfile.ZipFile(r"tmp\msgspec\artifact_artifact-sdist.zip")
z.extractall(r"tmp\msgspec\artifact_artifact-sdist")
print("extracted")
'@ | & "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe"
```

校验项：**文件大小 == `size_in_bytes`、sha256 == `digest`、zip 可完整解压**。校验不通过就**不要解压**，重新下载或报告问题。

### 6.5 移动 artifact 到 wheel 文件夹

将解压得到的 wheel 文件移动到 `wheel/{DEP_NAME}/{DEP_VERSION}` 文件夹中。

注意 wheel 文件夹只能存放来自 ci 构建的 wheel，禁止将本地构建的 wheel 放入。

## 7. 更新主仓库 py38deps

### 7.1 更新 submodule 引用

执行脚本更新所有 submodules 的引用

```bash
<python> -m scripts.update_submodules
```

### 7.2 更新 README.md 的适配表格

### 7.3 在主仓库提交更改

在主仓库 py38deps 提交更改，包括 submodules 的引用更新与 README.md 的更新，提交信息类似

```
Add: PyAv==18.1.0
Add: anyio==4.14.2
```

### 7.4 增加 tag

给 7.3 中的 commit 创建轻量标签（lightweight tag），注意不要创建附注标签（annotated tag），tag 名称类似：

```
20260816-anyio==4.14.2
20260815-cffi==2.1.1
```

# 迁移备忘录

## 构建前检查：自动生成的版本号

部分库使用 setuptools-scm / hatch-vcs 等工具根据 git 状态自动生成版本号。官方在 tag 上构建所以是正式版本；但我们的 backport 分支在 tag 之后有额外 commit，构建时版本号会自动变成下一个 dev 版本（实例：msgspec 生成 `0.21.2.dev30+g...`），导致 wheel 文件名出现 dev 版本号、无法对应 `wheel/{DEP}/{VERSION}` 目录。**构建前必须检查并处理。**

### 检查方法

1. 查看 `pyproject.toml` / `setup.py` / `setup.cfg`：
   - `[project]` 中 `dynamic = ["version"]`（或 setup.py 中 `use_scm_version` / versioneer）
   - build-system requires 包含 `setuptools-scm`、`hatch-vcs` 等
   - 存在 `[tool.setuptools_scm]` / `[tool.hatch.version]`（`source = "vcs"`）段
2. 安全的写法（无需处理）：
   - `version = "x.y.z"` 静态写死（anyio、cffi、hypercorn）
   - `dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {attr = "pkg.__version__"}`，且源码中 `__version__` 是硬编码字符串（h2、hpack、hyperframe、trio、wsproto、idna）
   - setup.py 从硬编码头文件宏读取版本（python-zstandard）

### 修改方案

#### 方案 A：静态版本（源码不依赖自动生成文件时，参考 attrs commit 162a9a4）

- `[project]` 加 `version = "x.y.z"`，`dynamic` 移除 `"version"`
- 删除 `[tool.setuptools_scm]` / `[tool.hatch.version]` 段
- 从 build-system requires 移除 scm 依赖

#### 方案 B：自定义 version_scheme（源码从 `_version.py` 导入 `__version__` 时，参考 msgspec）

官方产物（wheel/sdist）里包含 scm 生成的 `_version.py`，如果 `__init__.py` 依赖它，不要删掉机制，改为固定 scheme 让 scm 自动生成内容一致的文件：

```toml
[tool.setuptools_scm]
version_file = "src/msgspec/_version.py"   # 原有配置保留
parentdir_prefix_version = "msgspec-"       # 原有配置保留
version_scheme = "scm_version:scheme"       # 新增：固定版本
local_scheme = "no-local-version"           # 新增：去掉 git node/dirty 产生的 +g... local 段
```

项目根新建 `scm_version.py`：

```python
def scheme(version):
    """Always report the pinned backport version."""
    return "x.y.z"
```

还需：
- `MANIFEST.in` 加 `include scm_version.py`（保证 sdist 携带，从 sdist 构建 wheel 时 scheme 可导入）
- `setup.py` 顶部加 `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))`（PEP 517 隔离构建时项目根不在 sys.path，不加会报 `Couldn't find any implementations for entrypoint`）

### 注意与坑

- **不要打 tag 固定版本**：每次构建/修复问题都要删 tag 再打，维护成本高
- **不要用 `tag_regex` + `fallback_version`**：describe 成功时 tag 不匹配会直接报错而非 fallback；fallback 分支里 distance 仍参与计算，照样产出 dev 版本
- **验证时不要用 `setuptools_scm.get_version(root=".")`**：它是程序化 API，不读 pyproject 配置；用 `Configuration.from_file` 或直接构建 sdist 验证：

```powershell
cd "E:\ProgramData\Pycharm\py38deps\repo\msgspec"
@'
from setuptools_scm import Configuration
from setuptools_scm._get_version_impl import _get_version
cfg = Configuration.from_file("pyproject.toml")
print(_get_version(cfg, force_write_version_files=False))
'@ | & "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe"
```

- 构建 sdist 后检查：文件名与 PKG-INFO 版本正确、`_version.py` 由 scm 生成且版本正确、`scm_version.py` 在 sdist 中
- 无 tag 环境也要验证（CI shallow checkout 场景）：`git clone --no-tags --depth 1` 到临时目录再跑上述检查
- 版本升级时记得同步修改 `scm_version.py`（方案 B）或静态 `version`（方案 A）

## 本地 tox 验证（如果项目使用 tox）

如果项目使用了 tox（pyproject.toml 中有 `[tool.tox]` 或存在 `tox.ini`），**必须在本地完整模拟 CI 的 tox 运行**，不能只直接跑 pytest。直接跑 pytest 会漏掉 tox 特有的问题（依赖组解析、sdist 构建安装、coverage 命令、`base_python` 解释器解析、gh-actions 映射过滤等），反复 push 会浪费 CI 资源。

实例教训（hpack 移植，2026-08）：上游 pyproject.toml 写的是 `[tool.pytest]`（非标准键，pytest 官方是 `[tool.pytest.ini_options]`），pytest 9.x 恰好能读、pytest 8.x 读不到，导致 cp38/cp39 下 `testpaths` 失效、`bench/` 被收集、缺 pytest-benchmark 报 setup error——直接跑 `pytest tests/` 完全发现不了，只有完整 tox 模拟才暴露。

模拟方法：tox-gh-actions 会按**运行 tox 的解释器版本**匹配 gh-actions 映射，得出该 job 要运行的 env 列表，因此每个 job 用对应版本的 python 运行 tox（3.8 job 用 cp38 的 python）。

```powershell
# 1) 在对应版本环境安装 tox + tox-gh-actions（cp38 下自动解析到最后一个支持 3.8 的 tox 4.23.2）
& "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe" -m pip install "tox>=4.23.2" "tox-gh-actions"

# 2) tox 按 env 名找解释器（py38 -> python3.8），portable python 只有 python.exe，创建硬链接并加入 PATH
New-Item -ItemType HardLink -Path "envs\cp38\python3.8.exe" -Target "envs\cp38\python.exe"
$env:PATH = "E:\ProgramData\Pycharm\py38deps\envs\cp38;$env:PATH"

# 3) 设置 GitHub Actions 环境变量（必须与运行 tox 在同一条命令内，环境变量不跨命令保留）
$env:GITHUB_ACTIONS="true"; $env:GITHUB_WORKFLOW="CI"; $env:GITHUB_JOB="tox"
$env:GITHUB_RUN_ID="1"; $env:GITHUB_REF="refs/heads/master"; $env:GITHUB_EVENT_NAME="push"
$env:GITHUB_REPOSITORY="python-hyper/hpack"; $env:GITHUB_ACTOR="test"; $env:GITHUB_SHA="deadbeef"

# 4) 完整跑该 job 的 env（等价于 CI 的 Initialize + Test 两步）
cd "E:\ProgramData\Pycharm\py38deps\repo\<DEP_NAME>"
& "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe" -m tox --parallel auto
```

注意事项：

- `--notest` 只创建 env 并安装依赖，用于快速验证解释器查找、依赖解析、sdist 构建；完整验证必须跑 `tox --parallel auto`（等价 CI 的 `tox --parallel auto --notest` + `tox --parallel 0`）
- tox-gh-actions 只保留 gh-actions 映射中、且存在于 `env_list` 的 env（未定义的残留 env 名如 h2spec 会被静默过滤，不会报错）
- 映射里 `base_python` 指定了特定版本解释器的 env（如 packaging 的 `python3.14`），本地必须提供对应解释器（硬链接 + PATH），否则报 `could not find python interpreter matching any of the specs`——CI runner 的系统 python 未必有该版本，本地验证能提前发现这类问题
- 模拟完清理：删除硬链接与 `.tox/`、`dist/` 目录

## 版本 Cheatsheet（构建与测试工具速查）

以下版本限制基于 cp38 ~ cp314 全环境实测（hyperframe 移植时验证，2026-08），后续适配其他库时直接套用，无需再纠结版本选择。

### 核心原则

- 反向移植通常只改元数据（pyproject.toml / CI / CHANGELOG），**先检查所有源文件是否都以 `from __future__ import annotations` 开头**，是则源码一般无需回退（见下方语法速查）
- cp38 分支用旧版工具，cp39+ 分支保持上游版本要求，通过 PEP 508 环境标记（`; python_version < '3.9'`）区分
- 如果上游声明的工具版本要求高于 3.8 可解析的上限，cp38 下 `pip wheel .` / `pip install` 会直接报 `No matching distribution found`，这就是需要加环境标记的信号

### build-system：构建隔离依赖（cp38 构建 wheel 的硬约束）

| 包 | 最后支持 3.8 的版本 | 3.9+ 可用 | 说明 |
| --- | --- | --- | --- |
| setuptools | 75.3.2（75.4.0 起要求 >=3.9，83.0.0 起要求 >=3.10） | 75.4.0+ | cp38 分支必须 `<75.4` |
| wheel | 0.45.1（0.46.0 起要求 >=3.9） | 0.46.0+ | 上游若声明 wheel 依赖，cp38 分支必须 `<0.46` |

推荐写法（cp38 分支加环境标记；cp39+ 分支保留上游的版本要求即可）：

```toml
[build-system]
requires = [
  "setuptools>=68,<75.4 ; python_version < '3.9'",
  "setuptools>=82 ; python_version >= '3.9'",  # keep upstream requirement
  "wheel>=0.45.1,<0.46 ; python_version < '3.9'",
  "wheel>=0.46.3 ; python_version >= '3.9'",   # omit if upstream has no wheel dep
]
build-backend = "setuptools.build_meta"
```

### 测试依赖：pytest 生态（cp38 下安装的硬约束）

| 包 | 最后支持 3.8 的版本 | 3.9+ 可用 | 说明 |
| --- | --- | --- | --- |
| pytest | 8.3.5（8.4.0 起要求 >=3.9，9.0.0 起要求 >=3.10） | 8.4.0+ | 约束 `<9` 时 cp38 自动解析到 8.3.5，无需环境标记 |
| pytest-cov | 5.0.0（6.0.0 起要求 >=3.9） | 6.0.0+ | cp38 分支必须 `<6` |
| pytest-xdist | 3.6.x（3.7.0 起要求 >=3.9） | 3.7.0+ | 约束 `<4` 时 cp38 自动解析到 3.6.x，无需环境标记 |
| coverage | 7.6.1（7.7.0 起要求 >=3.9，作为 pytest-cov 间接依赖） | 7.7.0+ | pip 自动解析，无需单独处理 |

推荐写法：

```toml
testing = [
  "pytest>=8.3.3,<9",
  "pytest-cov>=6.0.0,<7 ; python_version >= '3.9'",
  "pytest-cov>=5.0.0,<6 ; python_version < '3.9'",
  "pytest-xdist>=3.6.1,<4",
]
```

### 源码语法速查：哪些写法在 3.8 下安全

只要源文件都以 `from __future__ import annotations` 开头：

- **安全**：注解中的 `int | None`、`list[X]`、`dict[K, V]`、`tuple[A, B]`、`type[X]` 等（注解被字符串化，不参与运行时求值）
- **安全**：函数体内的局部变量注解（如 `x: set[str] = set()`）——CPython 中局部变量注解从不求值（cp38 ~ cp314 行为一致）
- **需要回退**：运行时真正求值的写法，例如赋值表达式右侧的 `set[str]`、`isinstance(x, int | str)`、模块级类型别名 `X = int | str` 等（3.8 下报 `TypeError: 'type' object is not subscriptable`）

### 验证命令（cp38）

```powershell
# 1) 验证 build-system 在 cp38 下可解析（构建隔离）
cd repo/<DEP_NAME>
& "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe" -m pip wheel . --no-deps -w $env:TEMP\hwtest

# 2) 验证测试依赖在 cp38 下可安装
& "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe" -m pip install "pytest>=8.3.3,<9" "pytest-cov>=5.0.0,<6" "pytest-xdist>=3.6.1,<4"

# 3) 运行测试（纯 Python 库；src layout 时需要 PYTHONPATH）
$env:PYTHONPATH="src"
& "E:\ProgramData\Pycharm\py38deps\envs\cp38\python.exe" -m pytest tests/
```

### 其他

- ruff 的 `target-version` 改为 `"py38"`（ruff 是二进制工具，与 Python 版本无关，无需环境标记）
- tox 的 `env_list` / `gh-actions` 映射增加 `py38`，CI 矩阵增加 `"3.8"`
- twine 5.x 不支持 Metadata-Version 2.4（新版 setuptools 77+ 构建产物默认为 2.4，`twine check` 会报 `InvalidDistribution: Metadata is missing required fields: Name, Version`）。packaging 依赖建议：`"twine>=6.1.0,<6.2 ; python_version < '3.9'"` + `"twine>=6.2.0,<7 ; python_version >= '3.9'"`（twine 6.2 起要求 >=3.9）
- ruff 新版会把 preview 规则转正，导致 `lint.select = ["ALL"]` 下旧版源码的 lint 失败（实例：PLC0415 `import` should be at the top-level，v6.1.0 时代不报、ruff 0.16 起报）。应对：跟随上游的 `# noqa` 修复，而不是限制 ruff 版本
- 以最新发布版 tag 为基线，不要引入未发布 commit（见"反向移植流程"第 1 条）

### manylinux2014 镜像的 Python 支持（PyAV 移植时验证，2026-08）

- manylinux2014 镜像的 `latest`（2026 年）已移除 CPython 3.7/3.8 的 `/opt/python` 解释器，构建 cp37/cp38 会报 `executable doesn't exist in image`
- pypa/manylinux 于 **2025-05-08**（commit `87f462b` "Drop CPython 3.6 & 3.7"）从镜像移除 cp37
- **最后一个含 cp37 的镜像 tag：`quay.io/pypa/manylinux2014_x86_64:2025.05.03-1`**（i686/aarch64 同样，三个架构 tag 均存在且可拉取）
- 该镜像同时满足：
  - 含 CPython 3.7.17 / 3.8.20（2025-05-03 构建，早于 05-08 的移除 commit）
  - yum 源已指向 CentOS 7 vault（`fixup-mirrors.sh` 于 2024-07-01 引入），CentOS 7 EOL 后 `yum install` 仍可用
- 参考：cffi 的 cp38 构建用 `quay.io/pypa/manylinux2014:2026.05.02-2`（含 cp38，不含 cp37）
- manylinux-interpreters（镜像内工具）只能按需安装 PyPy/GraalPy，**不能**补装 CPython；CPython 必须预装于镜像
- cibuildwheel 配置示例：

  ```yaml
  CIBW_MANYLINUX_X86_64_IMAGE: quay.io/pypa/manylinux2014_x86_64:2025.05.03-1
  CIBW_MANYLINUX_I686_IMAGE: quay.io/pypa/manylinux2014_i686:2025.05.03-1
  CIBW_MANYLINUX_AARCH64_IMAGE: quay.io/pypa/manylinux2014_aarch64:2025.05.03-1
  ```

### musllinux 镜像的 Python 支持（PyAV 移植时验证，2026-08）

- musllinux_1_1 镜像官方已停止更新，**最后一个 tag：`quay.io/pypa/musllinux_1_1_x86_64:2024.06.22-2`**（aarch64 同样存在，`musllinux_1_1_aarch64:2024.06.22-2`），含 CPython 3.6 ~ 3.13（含 cp38/cp39/cp310）
- musllinux_1_2 镜像仍持续更新（如 `quay.io/pypa/musllinux_1_2_x86_64:2026.05.02-2` 含 cp38）
- auditwheel 的 musllinux_1_1 / 1_2 policy 完全相同（均无符号版本限制，1_1 priority 100 > 1_2 90），用 1_1 镜像构建会自动打 `musllinux_1_1` 标签
- 判断 vendor 库是否兼容 musl 1.1：对比其 UND 符号与 Alpine 3.11 的 musl 1.1.24 libc（`ld-musl-x86_64.so.1`）导出集。实测 pyav-ffmpeg 8.1.2-1 的 musllinux 库引用的 libc 符号全部在 musl 1.1.24 内，可同时构建 musllinux_1_1 与 1_2（cibuildwheel 分两次调用，各自设置 `CIBW_MUSLLINUX_*_IMAGE`，产物标签不同不会覆盖）
- 注意：musllinux 的 cp38 测试依赖 numpy 装不上（numpy 1.24.4 是最后支持 3.8 的版本且无 musllinux wheel），cp38-musllinux 需加入 `CIBW_TEST_SKIP`

## CI 配置：手动触发与产物上传

### fail-fast

矩阵 job 中某个版本失败时，默认（`fail-fast: true`）会取消其余还在运行的 job，一次只能看到第一个失败。移植后应设置 `fail-fast: false`，让所有版本跑完并一次性暴露所有问题，避免反复触发 CI：

```yaml
    strategy:
      fail-fast: false
      matrix:
        python-version:
        - "3.8"
        - "3.9"
        ...
```

### 手动触发与产物上传

官方仓库通常配置了自动发布流程（打 tag 自动构建并发布到 PyPI / GitHub Release），而我们的二次开发仓库无法自动发布，因此移植后的 CI 需要额外做两件事：

1. **手动触发按钮**：CI 增加 `workflow_dispatch` 触发事件，GitHub Actions 页面出现 "Run workflow" 按钮，可随时手动运行 CI
2. **产物上传**：构建产物（wheel / sdist）默认不会暴露为下载，需要 `upload-artifact` 上传后才会出现在 Actions 运行页面的 Artifacts 区域（保留 90 天）

```yaml
on:
  push:
    branches: ["master"]
  pull_request:
    branches: ["master"]
  workflow_dispatch:   # manual trigger button on GitHub Actions page
```

上传产物时注意：矩阵 job 中 tox-gh-actions 会把 packaging env 绑定到特定 python 版本（如 hyperframe 的 `3.9: py39, h2spec, lint, docs, packaging`），因此 upload step 需要加对应的 `if` 条件，避免其他 job 上传空产物：

```yaml
    - name: Upload dist
      if: matrix.python-version == '3.9'   # packaging env runs in this job only
      uses: actions/upload-artifact@v4
      with:
        name: dist
        path: dist/
```

注意事项：

- `workflow_dispatch` 需要 push 到 GitHub 后按钮才会出现
- 版本参考：msgspec 用 `upload-artifact@v5`，python-zstandard 用 `@v4.6.2`（pin SHA），hyperframe 用 `@v4`
- 纯 Python 库的 wheel 为 `py3-none-any`，一个产物即可覆盖 cp38 ~ cp314，无需按平台分别构建
