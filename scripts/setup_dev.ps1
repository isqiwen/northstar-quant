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
$EnvExample = Join-Path $ProjectRoot ".env.example"
$EnvSchemaSyncScript = Join-Path $ProjectRoot "scripts\dev\sync_env_schema.py"
$AppConfigExample = Join-Path $ProjectRoot "configs\app.example.yaml"
$AppConfig = Join-Path $ProjectRoot "configs\app.yaml"
$LegacyAppConfig = Join-Path $ProjectRoot "configs\app.local.yaml"

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

function Ensure-ActiveAppConfig {
    if (Test-Path -LiteralPath $LegacyAppConfig) {
        Fail "发现已废弃的 configs/app.local.yaml。请将需要保留的值完整迁入 configs/app.yaml，然后删除该文件。"
    }
    if (-not (Test-Path -LiteralPath $AppConfigExample)) {
        Fail "未找到 configs/app.example.yaml；无法创建活动应用配置。"
    }
    if (Test-Path -LiteralPath $AppConfig) {
        return
    }

    Copy-Item -LiteralPath $AppConfigExample -Destination $AppConfig
    Write-Host "已从 configs/app.example.yaml 创建本地活动配置 configs/app.yaml。"
}

function Ensure-ActiveEnvFile {
    if (-not (Test-Path -LiteralPath $EnvExample)) {
        Fail "未找到 .env.example；无法创建完整的活动环境文件。"
    }
    if (-not (Test-Path -LiteralPath $EnvSchemaSyncScript)) {
        Fail "未找到 scripts/dev/sync_env_schema.py；无法校验活动环境文件。"
    }
    if (Test-Path -LiteralPath $EnvFile) {
        return
    }

    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
    Write-Host "已从 .env.example 创建本地活动环境文件 .env。"
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

function Set-EnvFileValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    $escapedKey = [regex]::Escape($Key)
    $lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $Path -Encoding utf8)
    $replaced = $false
    for ($index = 0; $index -lt $lines.Count; $index += 1) {
        if ([regex]::IsMatch($lines[$index], "^\s*$escapedKey\s*=")) {
            $lines[$index] = "$Key=$Value"
            $replaced = $true
            break
        }
    }
    if (-not $replaced) {
        Fail "活动 .env 缺少字段 $Key；请先完成环境文件结构迁移。"
    }

    # .env.tmp.<GUID> 被 .gitignore 的 .env.* 覆盖；中断时也不会留下可误提交的密钥文件。
    $temporaryPath = Join-Path (Split-Path -Parent $Path) ("{0}.tmp.{1}" -f (Split-Path -Leaf $Path), [guid]::NewGuid().ToString("N"))
    try {
        [System.IO.File]::WriteAllLines(
            $temporaryPath,
            [string[]]$lines,
            (New-Object System.Text.UTF8Encoding($false))
        )
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function New-DevPostgresPassword {
    $bytes = New-Object byte[] 18
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($bytes)
    }
    finally {
        $random.Dispose()
    }
    return ([System.BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
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
Ensure-ActiveAppConfig
Ensure-ActiveEnvFile

Push-Location $ProjectRoot
try {
    Require-Command "uv" "请先安装 uv，再重新运行 scripts/setup_dev.ps1。"
    Require-Docker

    if (-not $SkipSync) {
        Invoke-Checked "同步锁定的 Python 开发依赖" { & uv sync --extra dev --locked }
    }

    Invoke-Checked "校验并迁移本地活动环境文件结构" {
        & uv run --no-sync python $EnvSchemaSyncScript --template $EnvExample --active $EnvFile --apply
    }

    $PostgresPassword = Get-EnvFileValue -Path $EnvFile -Key "POSTGRES_PASSWORD"
    if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
        $PostgresPassword = New-DevPostgresPassword
        Set-EnvFileValue -Path $EnvFile -Key "POSTGRES_PASSWORD" -Value $PostgresPassword
        Write-Host "已生成本地开发 PostgreSQL 密码并写入 .env。"
    }
    if ($PostgresPassword -match "[\r\n]") {
        Fail "POSTGRES_PASSWORD 不能包含换行符。"
    }

    $PostgresPort = Get-EnvFileValue -Path $EnvFile -Key "POSTGRES_PORT"
    if ([string]::IsNullOrWhiteSpace($PostgresPort)) {
        $PostgresPort = "5432"
        Set-EnvFileValue -Path $EnvFile -Key "POSTGRES_PORT" -Value $PostgresPort
    }
    $parsedPort = 0
    if (
        -not [int]::TryParse($PostgresPort, [ref]$parsedPort) -or
        $parsedPort -lt 1 -or
        $parsedPort -gt 65535
    ) {
        Fail "POSTGRES_PORT 必须是 1 到 65535 的整数。"
    }

    $encodedPassword = [System.Uri]::EscapeDataString($PostgresPassword)
    $databaseUrl = "postgresql+psycopg://northstar:{0}@127.0.0.1:{1}/northstar" -f `
        $encodedPassword, $parsedPort
    $testDatabaseUrl = "postgresql+psycopg://northstar:{0}@127.0.0.1:{1}/northstar_test" -f `
        $encodedPassword, $parsedPort
    Set-EnvFileValue -Path $EnvFile -Key "NORTHSTAR_DATABASE_URL" -Value $databaseUrl
    Set-EnvFileValue -Path $EnvFile -Key "NORTHSTAR_TEST_DATABASE_URL" -Value $testDatabaseUrl
    Set-EnvFileValue -Path $EnvFile -Key "NORTHSTAR_ENV" -Value "dev"
    Set-EnvFileValue -Path $EnvFile -Key "NORTHSTAR_BROKER" -Value "paper"
    Set-EnvFileValue -Path $EnvFile -Key "NORTHSTAR_LIVE_TRADING_ENABLED" -Value "false"
    Set-EnvFileValue -Path $EnvFile -Key "NORTHSTAR_KILL_SWITCH_ENABLED" -Value "false"

    # 当前 PowerShell 进程也显式使用同一套开发值，避免外层环境变量干扰初始化。
    $env:NORTHSTAR_DATABASE_URL = $databaseUrl
    $env:NORTHSTAR_TEST_DATABASE_URL = $testDatabaseUrl
    $env:NORTHSTAR_ENV = "dev"
    $env:NORTHSTAR_BROKER = "paper"
    $env:NORTHSTAR_LIVE_TRADING_ENABLED = "false"
    $env:NORTHSTAR_KILL_SWITCH_ENABLED = "false"

    Write-Host "本入口只会创建或迁移本地 .env、启动 Docker PostgreSQL，并运行迁移、健康检查、测试与静态检查。"
    Write-Host "不会下载市场数据、不会启动调度器或调用任何 live 命令。"

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
