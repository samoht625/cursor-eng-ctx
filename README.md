# eng ctx

A command-line tool that analyzes local git repository merges and scores them based on the AI Measurement Framework from the article ["Engineering KPIs in the Age of AI"](https://engineeredintelligence.substack.com/p/engineering-kpis-in-the-age-of-ai).

## Features

- Analyzes merge commits from local git repositories
- Analyzes merges using OpenAI's GPT-4 based on the DX AI Measurement Framework
- Scores merges on multiple dimensions:
  - AI Utilization
  - Code Quality Impact
  - Delivery Velocity
  - Innovation Level
  - Team Collaboration
- Saves results to SQLite database with caching for efficiency
- Provides web interface for viewing and filtering results
- No GitHub API key required - works with any local git repository

## Installation

1. Clone this repository:
```bash
git clone https://github.com/samoht625/cursor-eng-ctx.git
cd cursor-eng-ctx
```

2. (Optional) Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip3 install -r requirements.txt
```

4. Set up your OpenAI API key:

Create a `.env` file with your configuration:
```bash
cp .env.example .env
# Edit .env and add your OpenAI API key and repository path
```

### Getting API Keys

**OpenAI API Key:**
1. Go to https://platform.openai.com/api-keys
2. Click "Create new secret key"
3. Copy the generated key

**Repository Path:**
- Set `REPO_PATH` in your `.env` file to point to the repository you want to analyze
- Example: `REPO_PATH=/Users/yourname/projects/your-repo`
- This enables diff viewing in the web interface and makes the `--repo` argument optional for analysis

## Usage

### Basic Usage

The main parameter you need is `--since` to specify the time period to analyze:

```bash
python3 analyze_prs.py --since "1 week ago" --users users-config.txt
```

**Required parameters:**
- `--since`: Time period to analyze (e.g., "1 week ago", "2 months ago", "2024-01-01")
- `--users`: List of users to analyze (CSV string or path to config file)

Make sure you have set up your `.env` file with your OpenAI API key and repository path before running the analysis.

### Command-Line Options

**Main Parameter:**
- `--since`: Timestamp or relative time (required)
  - Examples: `"1 week ago"`, `"2 months ago"`, `"2024-01-01"`

**Advanced Parameters:**
- `--users`: CSV of users or path to config file (required)
  - Examples: `"user1,user2,user3"` or `users-config.txt`
  
- `--model`: OpenAI model to use (default: `gpt-4`, can also use `gpt-3.5-turbo` for lower costs)

- `--clear-cache`: Clear the LLM response cache before running

- `--cache-stats`: Show cache statistics and exit

### Examples

**Basic usage - just specify the time period:**
```bash
python3 analyze_prs.py --since "1 week ago" --users users-config.txt
```

**Analyze different time periods:**
```bash
# Last 2 weeks
python3 analyze_prs.py --since "2 weeks ago" --users "rnvarma,ehong97,ajhoffman"

# Last month
python3 analyze_prs.py --since "1 month ago" --users users-config.txt

# Since specific date
python3 analyze_prs.py --since "2024-01-01" --users users-config.txt
```

**Advanced options:**
```bash
# Use GPT-3.5-turbo for lower costs
python3 analyze_prs.py --since "1 week ago" --users users-config.txt --model gpt-3.5-turbo
```

## Output

The tool saves analysis results to a SQLite database and provides a web interface for viewing results.

### Viewing Results

After running the analysis, start the web app:

```bash
python3 web_app.py
```

Then open http://localhost:8080 in your browser to view:

- **Dashboard** with summary statistics and interactive filtering
- **Author Performance** breakdown by individual contributors  
- **Detailed Analysis Table** with scores, summaries, and sorting options
- **Real-time Filtering** by author and various sorting criteria

### Database Storage

Analysis data is stored in SQLite database (`pr_analysis.db`) with:
- Individual merge details and scores
- Cached LLM responses to avoid duplicate API calls
- Full audit trail with timestamps

## Scoring Dimensions

Based on the DX AI Measurement Framework, merges are scored on:

1. **AI Utilization (0-10)**: Evidence of AI tool usage in development
2. **Code Quality Impact (0-10)**: Quality and maintainability of changes
3. **Delivery Velocity (0-10)**: Speed and efficiency of delivery
4. **Innovation Level (0-10)**: Technical innovation and problem-solving
5. **Team Collaboration (0-10)**: Evidence of effective collaboration
6. **Overall Score (0-100)**: Weighted combination of all dimensions

## Users Config File Format

The `users-config.txt` file should contain one git author name per line:

```
rnvarma
ehong97
ajhoffman
# Comments are supported with #
```

**Note:** The author names should match exactly what appears in your git commit history. You can check author names with:
```bash
git log --pretty=format:"%an" | sort | uniq
```

## How It Works

The tool:
1. Finds all merge commits in the specified time period
2. For each merge, identifies commits that were part of the "PR" (commits between the merge and its parent)
3. Filters for commits by the specified authors
4. Analyzes each merge using OpenAI to score it on the AI Measurement Framework
5. Saves results to SQLite database with caching for efficiency

## Troubleshooting

**Common Issues:**
- "Not a git repository": Make sure the `REPO_PATH` in your `.env` file points to a valid git repository
- "No merge commits found": The repository might not have merge commits in the specified time period
- "No merges found involving the specified users": Check that the author names match exactly
- "No diff content available": Make sure `REPO_PATH` is set in your `.env` file to point to the repository you analyzed
- OpenAI errors: Verify your API key in the `.env` file and available credits

## Cost Considerations

- OpenAI API usage is charged per token
- Each merge analysis uses approximately 500-1000 tokens
- Estimate: ~$0.03-0.06 per merge with GPT-4
- Consider using GPT-3.5-turbo for lower costs

## License

MIT License
