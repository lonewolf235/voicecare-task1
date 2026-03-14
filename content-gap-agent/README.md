# VoiceCare AI Content Gap Agent

An autonomous research and pipeline agent that crawls competitor websites, identifies strategic content gaps, and uses OpenAI to generate rich, SEO-optimized health-tech video scripts that get natively exported to Notion and Slack.

Designed to be executed weekly via n8n workflow automation.

---

## 🎯 Features

- **Multi-Site Crawling:** Leverages the native Firecrawl API to dynamically scrape all pages from your own domain and tracked competitors. Features 429 backoff rate limiting and permanent local JSON caching to save API credits.
- **Topographical AI Gap Analysis:** Extracts the Titles and content from each page, embeds them via OpenAI ADA, and checks cosine similarity to detect hidden content strategies you are missing.
- **Semrush Keyword Integration:** Weighs competitor gaps against actual search volumes to rank what gap is most critical to address first.
- **Autonomous Script Writing:** Instructs `gpt-4o` to write fully formatted TikTok/Reels scripts complete with social captions, hashtags, and visual hooks.
- **Direct Notion Pipeline:** Skips raw JSON logging by instantly mapping new scripts to a native Notion Rich-Text Board, bypassing character limits.
- **Daily Slack Summaries:** Webhook alerts that drop the generated Notion script links right into your content marketing team channel.

---

## 🚀 Setup & Installation

### 1. Requirements

- Python 3.10+
- A running [n8n](https://n8n.io/) instance (Local or Cloud)
- API Keys for OpenAI, Firecrawl, and Notion.

### 2. Install Dependencies

Clone this repository and create a virtual environment:

```bash
git clone https://github.com/bpgp24shubhama-ai/voicecare-task1.git
cd voicecare-task1/content-gap-agent

python -m venv venv
# Windows
venv\Scripts\activate.bat
# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configuration 

Copy the `.env.example` file and configure your API keys:

```bash
cp .env.example .env
```

**Required Keys:**
* `OPENAI_API_KEY`: Used to generate embeddings during Gap Analysis and for video scripts.
* `FIRECRAWL_API_KEY`: Required to crawl and monitor competitors.
* `NOTION_API_KEY` (Integration Token): Needed to automate Notion Database entries. [Create one here](https://www.notion.so/my-integrations).
* `NOTION_DATABASE_ID`: The UUID of the Database where pages should be created. **Important: You must manually click `...` and share the database with your Notion integration beforehand!**

*You can also customize the sites targeted in `config/sites.yaml`.*

---

## 🧠 Usage

### Manual Execution
You can test the module locally to see the logs in your terminal and verify your cache outputs by running:

```bash
python main.py
```

*Cache Notes: Competitor scraped data is saved to `cache/[MD5_HASH].json` on your hard drive. If you run the command again within 24 hours, the crawler intercepts it instantly.*

### n8n Automated Execution

We provide a built-in workflow template (`n8n-content-gap-workflow.json`) to automate this pipeline out of the box.

1. In your n8n dashboard, click **Add Workflow** -> **Import from File**.
2. Select the `n8n-content-gap-workflow.json` file in the root directory.
3. Replace the `f:\VoiceCare_Assignment\...` placeholders in the `Execute Command` Node manually with the absolute paths to your local project root and your Virtual Environment python executable (e.g. `/usr/bin/python3 /path/content-gap-agent/main.py`).
4. Activate the node and press **Test Workflow**! If it works, the "Success" notification will pipe the resulting Notion URL strings directly back into your n8n workspace or Slack API!

--- 

## 🏗️ Architecture Modules

* `agents/crawler.py` – Firecrawl extraction module featuring local filesystem caching.
* `agents/gap_analyzer.py` – Embeds data, does Cosine matching against `sites.yaml` rules, outputs ranked CSVs.
* `agents/script_writer.py` – Prompts `gpt-4o` with the Gap data and target audience system prompts.
* `agents/notion_exporter.py` – Handles standardizing text limits into API-friendly array blocks. 
* `agents/reporter.py` – Writes the run summaries to physical JSON logs and executes Slack webhooks.

---

> Built with Antigravity during the Advanced Agentic Coding Project.
