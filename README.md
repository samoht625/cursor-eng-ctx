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
git clone <repo-url>
cd eng-context
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

4. Set up your OpenAI API key (choose one method):

**Option A: .env file (recommended):**
```bash
cp .env.example .env
# Edit .env and replace with your actual API key and repository path
```

**Option B: Environment variable:**
```bash
export OPENAI_API_KEY=your_openai_api_key_here
```

**Option C: Command line argument:**
```bash
python3 analyze_prs.py --openai-key your_openai_api_key_here --since "1 week ago" --users users-config.txt --repo /path/to/your/repo
```

### Getting API Keys

**OpenAI API Key:**
1. Go to https://platform.openai.com/api-keys
2. Click "Create new secret key"
3. Copy the generated key

**Repository Path:**
- Both the analysis script and web interface can use the `REPO_PATH` environment variable
- Set `REPO_PATH` in your `.env` file to point to the repository you want to analyze
- Example: `REPO_PATH=/Users/yourname/projects/your-repo`
- For analysis: makes `--repo` argument optional
- For web interface: enables diff viewing when repo_path isn't stored in database
- If not set, analysis script requires `--repo` argument, web app defaults to current directory

## Usage

### Basic Usage

Analyze merges from the last week for users in the config file:

**If you use a .env file (recommended):**
```bash
cp .env.example .env
# Edit .env and add your API key and repository path, then run:
python3 analyze_prs.py --since "1 week ago" --users users-config.txt
# The --repo argument is optional if REPO_PATH is set in .env
```

**If you set the environment variable:**
```bash
export OPENAI_API_KEY=your_api_key_here
python3 analyze_prs.py --since "1 week ago" --users users-config.txt --repo /path/to/your/repo
```

**Or pass it directly:**
```bash
python3 analyze_prs.py --openai-key your_api_key_here --since "1 week ago" --users users-config.txt --repo /path/to/your/repo
```

### Command-Line Options

- `--since`: Timestamp or relative time (required)
  - Examples: `"1 week ago"`, `"2 months ago"`, `"2024-01-01"`
  
- `--users`: CSV of users or path to config file (required)
  - Examples: `"user1,user2,user3"` or `users-config.txt`
  
- `--repo`: Path to local git repository (or set REPO_PATH environment variable)
  


- `--model`: OpenAI model to use (default: `gpt-4`, can also use `gpt-3.5-turbo` for lower costs)

- `--openai-key`: OpenAI API key (optional if set in .env file or OPENAI_API_KEY environment variable)

- `--clear-cache`: Clear the LLM response cache before running

- `--cache-stats`: Show cache statistics and exit

### Examples

1. Analyze merges from the last 2 weeks for specific users:
```bash
python3 analyze_prs.py --since "2 weeks ago" --users "rnvarma,ehong97,ajhoffman" --repo /path/to/repo
# Or if REPO_PATH is set in .env:
python3 analyze_prs.py --since "2 weeks ago" --users "rnvarma,ehong97,ajhoffman"
```

2. Use a config file for users:
```bash
python3 analyze_prs.py --since "1 month ago" --users users-config.txt --repo /path/to/repo
```

3. Analyze merges since a specific date:
```bash
python3 analyze_prs.py --since "2024-01-01" --users users-config.txt
# Assumes REPO_PATH is set in .env
```

4. Use GPT-3.5-turbo for lower costs:
```bash
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
- "Not a git repository": Make sure the path points to a valid git repository
- "No merge commits found": The repository might not have merge commits in the specified time period
- "No merges found involving the specified users": Check that the author names match exactly
- "No diff content available": Set `REPO_PATH` in your `.env` file to point to the repository you analyzed
- OpenAI errors: Verify your API key and available credits

## Cost Considerations

- OpenAI API usage is charged per token
- Each merge analysis uses approximately 500-1000 tokens
- Estimate: ~$0.03-0.06 per merge with GPT-4
- Consider using GPT-3.5-turbo for lower costs

## License

MIT License
