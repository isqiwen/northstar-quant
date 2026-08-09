[CmdletBinding()]
param(
    # 已具备锁定依赖时可跳过同步；默认始终使用 uv.lock 同步开发依赖。
    [switch]$SkipSync,
    # 仅在排查初始化问题时跳过完整测试；默认执行全部测试。
    [switch]$SkipTests,
    # 仅在排查初始化问题时跳过 Ruff；默认执行静态检查。
    [switch]$SkipLint
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"

function Write-Step {
    param([string]$Message)

    Write-Host "`n==> $Message"
}

function Fail {
    param([string]$Message)

    throw $Message
}

function Require-Command {
    param(
        [string]$Name,
        [string]$HelpText
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "未找到 $Name。$HelpText"
    }
}

function Get-EnvFileValue {
    param(
        [string]$Path,
        [string]$Key
    )

    $escapedKey = [regex]::Escape($Key)
    foreach ($line in Get-Content -LiteralPath $Path -Encoding utf8) {
        $match = [regex]::Match($line, "^\s*$escapedKey\s*=\s*(?<value>.*)$")
        if (-not $match.Success) {
            continue
        }

        $value = $match.Groups["value"].Value.Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        return $value
    }

    return $null
}

function Invoke-Checked {
    param(
        [string]$Description,
        [scriptblock]$Command
    )

    Write-Step $Description
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Fail "$Description 失败，退出码：$LASTEXITCODE。"
    }
}

function Require-Docker {
    Require-Command "docker" "请安装并启动 Docker Desktop，然后重新执行本脚本。"

    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "当前 Docker 不支持 Compose v2。请在 Docker Desktop 中启用/升级 Compose 后重试。"
    }

    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker daemon 尚未运行。请启动 Docker Desktop 并确认 docker info 可用后重试。"
    }
}

function Wait-ForPostgres {
    for ($attempt = 1; $attempt -le 30; $attempt += 1) {
        & docker compose exec -T postgres pg_isready -U northstar -d postgres *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Seconds 2
    }

    & docker compose logs --tail=80 postgres
    Fail "PostgreSQL 在 60 秒内未就绪。"
}

function Ensure-Database {
    param([string]$DatabaseName)

    $databases = & docker compose exec -T postgres psql -U northstar -d postgres -Atc `
        "SELECT datname FROM pg_database"
    if ($LASTEXITCODE -ne 0) {
        Fail "无法检查数据库 $DatabaseName。"
    }

    if ($databases -contains $DatabaseName) {
        return
    }

    & docker compose exec -T postgres createdb -U northstar $DatabaseName
    if ($LASTEXITCODE -ne 0) {
        Fail "无法创建数据库 $DatabaseName。"
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "compose.yaml"))) {
    Fail "未找到 compose.yaml；请从项目根目录的完整工作树运行本脚本。"
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Fail "未找到本地 .env。请先手动复制 .env.example 为 .env，并设置非空 POSTGRES_PASSWORD；本脚本不会创建或修改 .env。"
}

$PostgresPassword = Get-EnvFileValue -Path $EnvFile -Key "POSTGRES_PASSWORD"
if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
    Fail "POSTGRES_PASSWORD 不能为空。请在本地 .env 中设置后重试；本脚本不会修改该文件。"
}
if ($PostgresPassword -match "[\r\n]") {
    Fail "POSTGRES_PASSWORD 不能包含换行符。"
}

$PostgresPort = Get-EnvFileValue -Path $EnvFile -Key "POSTGRES_PORT"
if ([string]::IsNullOrWhiteSpace($PostgresPort)) {
    $PostgresPort = "5432"
}
$parsedPort = 0
if (
    -not [int]::TryParse($PostgresPort, [ref]$parsedPort) -or
    $parsedPort -lt 1 -or
    $parsedPort -gt 65535
) {
    Fail "POSTGRES_PORT 必须是 1 到 65535 的整数。"
}

# 只在当前 PowerShell 进程及其子进程中注入连接串和安全模式，不写回 .env。
$encodedPassword = [System.Uri]::EscapeDataString($PostgresPassword)
$env:NORTHSTAR_DATABASE_URL = "postgresql+psycopg://northstar:{0}@127.0.0.1:{1}/northstar" -f `
    $encodedPassword, $parsedPort
$env:NORTHSTAR_TEST_DATABASE_URL = "postgresql+psycopg://northstar:{0}@127.0.0.1:{1}/northstar_test" -f `
    $encodedPassword, $parsedPort
$env:NORTHSTAR_ENV = "dev"
$env:NORTHSTAR_BROKER = "paper"
$env:NORTHSTAR_LIVE_TRADING_ENABLED = "false"

Write-Host "本入口只会启动本地 Docker PostgreSQL，并运行迁移、健康检查、测试与静态检查。"
Write-Host "不会修改 .env、不会下载市场数据、不会启动调度器或调用任何 live 命令。"

Push-Location $ProjectRoot
try {
    Require-Command "uv" "请先安装 uv，再重新运行 scripts/setup_dev.ps1。"
    Require-Docker

    if (-not $SkipSync) {
        Invoke-Checked "同步锁定的 Python 开发依赖" { & uv sync --extra dev --locked }
    }

    Invoke-Checked "启动本地 Docker PostgreSQL" { & docker compose up -d postgres }
    Write-Step "等待 PostgreSQL 就绪"
    Wait-ForPostgres
    Write-Step "确认开发与测试数据库"
    Ensure-Database "northstar"
    Ensure-Database "northstar_test"

    Invoke-Checked "执行数据库迁移" { & uv run alembic upgrade head }
    Invoke-Checked "运行只读健康检查" { & uv run northstar health }

    if (-not $SkipTests) {
        Invoke-Checked "运行完整测试套件" { & uv run pytest }
    }
    if (-not $SkipLint) {
        Invoke-Checked "运行 Ruff 静态检查" { & uv run ruff check . }
    }
}
finally {
    Pop-Location
}

Write-Host "`n开发/测试环境已就绪：应用库 northstar，隔离测试库 northstar_test。"
