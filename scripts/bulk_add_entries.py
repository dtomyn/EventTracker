from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

# A curated set of recent URLs (OpenAI, Anthropic, GitHub Copilot, AWS Kiro, VS Code)
URLS = [
    "https://openai.com/index/our-approach-to-advertising-and-expanding-access/",
    "https://arstechnica.com/information-technology/2026/01/openai-to-test-ads-in-chatgpt-as-it-burns-through-billions/",
    "https://techcrunch.com/2026/01/14/openai-signs-deal-reportedly-worth-10-billion-for-compute-from-cerebras/",
    "https://www.cnbc.com/2026/02/27/open-ai-funding-round.html",
    "https://techcrunch.com/2026/02/27/openai-raises-110b-in-one-of-the-largest-private-funding-rounds-in-history/",
    "https://astral.sh/blog/openai",

    "https://www.anthropic.com/news/introducing-anthropic-labs",
    "https://techcrunch.com/2026/01/09/anthropic-adds-allianz-to-growing-list-of-enterprise-wins/",
    "https://www.reuters.com/business/openai-courts-private-equity-join-enterprise-ai-venture-sources-say-2026-03-16/",
    "https://apnews.com/article/anthropic-ai-pentagon-2026",
    "https://time.com/article/2026/03/11/anthropic-claude-disruptive-company/",
    "https://support.claude.com/en/articles/12138966-release-notes",

    "https://github.blog/changelog/2026-03-04-github-copilot-in-visual-studio-february-update/",
    "https://github.blog/changelog/2026-02-25-github-copilot-cli-is-now-generally-available/",
    "https://github.blog/changelog/2026-03-05-gpt-5-4-is-generally-available-in-github-copilot/",
    "https://github.com/features/copilot/whats-new",
    "https://github.blog/changelog/2026-02-04-github-copilot-in-visual-studio-code-v1_109/",
    "https://github.blog/changelog/2026-03-13-updates-to-github-copilot-for-students/",

    "https://aws.amazon.com/blogs/aws/aws-weekly-roundup-claude-sonnet-4-6-in-amazon-bedrock-kiro-in-govcloud-regions-new-agent-plugins-and-more-february-23-2026/",
    "https://kiro.dev/blog/opus-4-6/",
    "https://aws.amazon.com/about-aws/whats-new/2026/03/aws-sam-kiro-power/",
    "https://www.techradar.com/pro/aws-launches-kiro-an-agentic-ai-ide-to-end-the-chaos-of-vibe-coding",
    "https://kiro.dev/changelog/",
    "https://www.timesnownews.com/technology-science/amazons-ai-bot-kiro-took-its-web-service-down-for-13-hours-heres-what-happened-article-153655493",

    "https://code.visualstudio.com/updates/v1_109",
    "https://code.visualstudio.com/updates/v1_110",
    "https://code.visualstudio.com/updates/v1_111",
    "https://github.com/microsoft/vscode/releases",
    "https://devblogs.microsoft.com/vscode-blog",
    "https://releasebot.io/updates/microsoft/visual-studio-code",
]


def infer_date_from_url(url: str) -> tuple[int, int]:
    # Try to find /YYYY/MM/DD or /YYYY/MM or /YYYY-MM-DD patterns
    m = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12][0-9]|3[01])", url)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])", url)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12][0-9]|3[01])", url)
    if m:
        return int(m.group(1)), int(m.group(2))
    # fallback to current year/month (assume recent)
    return 2026, 3


def safe_title_from_url(url: str) -> str:
    # use hostname + tail path as a short title
    p = Path(url.replace("https://", "").replace("http://", ""))
    name = p.parts[-1] or p.parts[-2] if len(p.parts) > 1 else p.parts[0]
    return f"Imported: {name}"


def run_cli_for_url(url: str) -> None:
    year, month = infer_date_from_url(url)
    title = safe_title_from_url(url)
    final_text = f"Imported from {url}"
    tags = "news,ai"
    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "scripts.add_entry",
        "--title",
        title,
        "--group-id",
        "default",
        "--year",
        str(year),
        "--month",
        str(month),
        "--final-text",
        final_text,
        "--tags",
        tags,
        "--source-url",
        url,
    ]
    print("Running:", " ".join(shlex.quote(p) for p in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    print("Exit:", result.returncode)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())


def main() -> None:
    for url in URLS:
        run_cli_for_url(url)


if __name__ == "__main__":
    main()
