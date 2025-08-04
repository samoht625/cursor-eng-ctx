#!/usr/bin/env python3
"""
eng ctx
Analyzes local git repository PRs and scores them based on the AI Measurement Framework
"""

import os
import sys
import json
import subprocess
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import click

from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Database configuration
CACHE_DB_PATH = "db/llm_cache.db"
ANALYSIS_DB_PATH = "db/pr_analysis.db"

# Impact scoring criteria
SCORING_CRITERIA = """
You are analyzing a software engineering merge (equivalent to a Pull Request) to determine its impact score.

Evaluate the merge on these criteria and assign an impact score from 1-5:

Impact Score (1-5):
1 = Very Low Impact: Minor changes, small bug fixes, config tweaks
2 = Low Impact: Small features, isolated improvements, minor refactoring  
3 = Medium Impact: Moderate features, some architectural changes, new components
4 = High Impact: Major features, significant architectural changes, new systems
5 = Very High Impact: Large-scale changes, major architectural overhauls, new dependencies/frameworks

Consider these factors:
- Scope: How much of the codebase is affected?
- Complexity: How technically complex is the implementation?
- Architectural blast radius: How many systems/components are impacted?
- New dependencies: Are new frameworks, libraries, or external dependencies introduced?

Provide your assessment in this exact JSON format:
{
  "impact_score": [1-5],
  "impact_assessment": "[150-250 character rationale explaining the score based on scope, complexity, blast radius, and dependencies]"
}
"""


def init_cache_db():
    """Initialize the SQLite cache database."""
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    
    # Create table for LLM response cache
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS llm_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_hash TEXT UNIQUE NOT NULL,
            prompt_content TEXT NOT NULL,
            response_content TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create index for faster lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_prompt_hash ON llm_cache(prompt_hash)
    ''')
    
    conn.commit()
    conn.close()


def init_analysis_db():
    """Initialize the SQLite analysis database."""
    conn = sqlite3.connect(ANALYSIS_DB_PATH)
    cursor = conn.cursor()
    
    # Create table for PR analysis results
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pr_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merge_hash TEXT UNIQUE NOT NULL,
            merge_subject TEXT NOT NULL,
            merge_message TEXT,
            author TEXT NOT NULL,
            merge_date TIMESTAMP NOT NULL,
            commits_count INTEGER NOT NULL,
            additions INTEGER NOT NULL,
            deletions INTEGER NOT NULL,
            files_changed INTEGER NOT NULL,
            development_hours REAL NOT NULL,
            review_hours REAL NOT NULL,
            impact_score INTEGER NOT NULL,
            impact_assessment TEXT NOT NULL,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Check if merge_message column exists and add it if it doesn't
    cursor.execute("PRAGMA table_info(pr_analysis)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'merge_message' not in columns:
        cursor.execute('ALTER TABLE pr_analysis ADD COLUMN merge_message TEXT')
    
    # Add repo_path column if it doesn't exist
    if 'repo_path' not in columns:
        cursor.execute('ALTER TABLE pr_analysis ADD COLUMN repo_path TEXT')
    
    # Migrate to new simplified schema if needed
    if 'ai_utilization_score' in columns and 'impact_score' not in columns:
        # Add new columns
        cursor.execute('ALTER TABLE pr_analysis ADD COLUMN impact_score INTEGER')
        cursor.execute('ALTER TABLE pr_analysis ADD COLUMN impact_assessment TEXT')
        # Set default values for existing records
        cursor.execute('UPDATE pr_analysis SET impact_score = 3, impact_assessment = "Legacy data - not assessed with new criteria" WHERE impact_score IS NULL')
    
    # Create indexes for faster lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_merge_hash ON pr_analysis(merge_hash)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_author ON pr_analysis(author)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_merge_date ON pr_analysis(merge_date)
    ''')
    
    conn.commit()
    conn.close()


def save_analysis_to_db(scored_prs: List[Dict]):
    """Save analysis results to database."""
    conn = sqlite3.connect(ANALYSIS_DB_PATH)
    cursor = conn.cursor()
    
    for pr in scored_prs:
        cursor.execute('''
            INSERT OR REPLACE INTO pr_analysis (
                merge_hash, merge_subject, merge_message, author, merge_date, commits_count,
                additions, deletions, files_changed, development_hours, review_hours,
                impact_score, impact_assessment, repo_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            pr['merge_hash'], pr['merge_subject'], pr.get('merge_message', ''), pr['author'], pr['merge_date'],
            pr['commits_count'], pr['additions'], pr['deletions'], pr['files_changed'],
            pr['development_hours'], pr['review_hours'], pr['impact_score'], pr['impact_assessment'],
            pr.get('repo_path', '')
        ))
    
    conn.commit()
    conn.close()
    click.echo(f"  Analysis results saved to database: {len(scored_prs)} records")


def get_prompt_hash(prompt_content: str, model: str) -> str:
    """Generate a hash for the prompt content and model."""
    combined = f"{model}:{prompt_content}"
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()


def get_cached_response(prompt_content: str, model: str) -> Optional[str]:
    """Get cached LLM response if it exists."""
    prompt_hash = get_prompt_hash(prompt_content, model)
    
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        'SELECT response_content FROM llm_cache WHERE prompt_hash = ?',
        (prompt_hash,)
    )
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else None


def cache_response(prompt_content: str, model: str, response_content: str):
    """Cache an LLM response."""
    prompt_hash = get_prompt_hash(prompt_content, model)
    
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO llm_cache 
            (prompt_hash, prompt_content, response_content, model)
            VALUES (?, ?, ?, ?)
        ''', (prompt_hash, prompt_content, response_content, model))
        
        conn.commit()
    except sqlite3.Error as e:
        click.echo(f"Error caching response: {e}", err=True)
    finally:
        conn.close()


def parse_relative_date(date_string: str) -> datetime:
    """Parse relative date strings like '1 week ago' or absolute dates."""
    date_string = date_string.strip().lower()
    
    # Try to parse as absolute date first
    try:
        return date_parser.parse(date_string)
    except:
        pass
    
    # Parse relative dates
    now = datetime.now()
    
    # Remove 'ago' if present
    if date_string.endswith(' ago'):
        date_string = date_string[:-4].strip()
    
    # Split into number and unit
    parts = date_string.split()
    if len(parts) != 2:
        raise ValueError(f"Invalid relative date format: {date_string}")
    
    try:
        amount = int(parts[0])
    except ValueError:
        raise ValueError(f"Invalid number in relative date: {parts[0]}")
    
    unit = parts[1].rstrip('s')  # Remove plural 's'
    
    if unit in ['day', 'days']:
        return now - timedelta(days=amount)
    elif unit in ['week', 'weeks']:
        return now - timedelta(weeks=amount)
    elif unit in ['month', 'months']:
        return now - relativedelta(months=amount)
    elif unit in ['year', 'years']:
        return now - relativedelta(years=amount)
    else:
        raise ValueError(f"Unknown time unit: {unit}")


def run_git_command(cmd: List[str], cwd: str) -> str:
    """Run a git command and return the output."""
    try:
        result = subprocess.run(
            ['git'] + cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        click.echo(f"Git command failed: {' '.join(cmd)}", err=True)
        click.echo(f"Error: {e.stderr}", err=True)
        sys.exit(1)


def get_merge_commits(repo_path: str, since_date: datetime) -> List[Dict]:
    """Get merge commits and PR commits from the local repository."""
    since_str = since_date.strftime('%Y-%m-%d')
    
    # Get traditional merge commits
    cmd = ['log', '--merges', '--since', since_str, '--pretty=format:%H|%an|%ad|%s', '--date=iso']
    output = run_git_command(cmd, repo_path)
    
    merge_commits = []
    
    # Process traditional merge commits
    if output:
        for line in output.split('\n'):
            if not line.strip():
                continue
            
            parts = line.split('|')
            if len(parts) != 4:
                continue
            
            commit_hash, author, date_str, subject = parts
            
            # Parse the commit date
            try:
                commit_date = date_parser.parse(date_str)
            except:
                continue
            
            merge_commits.append({
                'hash': commit_hash,
                'author': author,
                'date': commit_date,
                'subject': subject,
                'is_traditional_merge': True
            })
    
    # Also get commits that look like squashed PRs (contain PR numbers like #12345)
    cmd = ['log', '--since', since_str, '--pretty=format:%H|%an|%ad|%s', '--date=iso', '--grep=#[0-9]']
    output = run_git_command(cmd, repo_path)
    
    if output:
        for line in output.split('\n'):
            if not line.strip():
                continue
            
            parts = line.split('|')
            if len(parts) != 4:
                continue
            
            commit_hash, author, date_str, subject = parts
            
            # Skip if this is already a traditional merge commit
            if any(mc['hash'] == commit_hash for mc in merge_commits):
                continue
            
            # Parse the commit date
            try:
                commit_date = date_parser.parse(date_str)
            except:
                continue
            
            # Only include if it has a PR number pattern
            import re
            if re.search(r'#\d+', subject):
                merge_commits.append({
                    'hash': commit_hash,
                    'author': author,
                    'date': commit_date,
                    'subject': subject,
                    'is_traditional_merge': False
                })
    
    return merge_commits


def get_commit_details(repo_path: str, commit_hash: str) -> Dict:
    """Get detailed information about a commit."""
    # Get commit details
    cmd = ['show', '--stat', '--format=format:%H|%an|%ae|%ad|%s|%b', '--date=iso', commit_hash]
    output = run_git_command(cmd, repo_path)
    
    if not output:
        return {}
    
    lines = output.split('\n')
    if not lines:
        return {}
    
    # Parse commit header
    header_parts = lines[0].split('|')
    if len(header_parts) < 5:
        return {}
    
    commit_hash, author, email, date_str, subject = header_parts[:5]
    body = '|'.join(header_parts[5:]) if len(header_parts) > 5 else ''
    
    # Parse stats
    additions = 0
    deletions = 0
    files_changed = 0
    
    for line in lines:
        if 'file changed' in line or 'files changed' in line:
            # Format: " 1 file changed, 5 insertions(+), 1 deletion(-)"
            # or: " 3 files changed, 45 insertions(+), 12 deletions(-)"
            try:
                parts = line.split(',')
                if len(parts) >= 1:
                    files_part = parts[0].strip()
                    files_changed = int(files_part.split()[0])
                
                for part in parts[1:]:
                    part = part.strip()
                    if 'insertion' in part:
                        additions = int(part.split()[0])
                    elif 'deletion' in part:
                        deletions = int(part.split()[0])
            except (ValueError, IndexError):
                # If parsing fails, continue to next line
                continue
            break
    
    try:
        commit_date = date_parser.parse(date_str)
    except:
        commit_date = datetime.now()
    
    return {
        'hash': commit_hash,
        'author': author,
        'email': email,
        'date': commit_date,
        'subject': subject,
        'body': body,
        'additions': additions,
        'deletions': deletions,
        'files_changed': files_changed
    }


def get_parent_commits(repo_path: str, commit_hash: str) -> List[str]:
    """Get parent commit hashes."""
    cmd = ['log', '--pretty=format:%H', '-n', '1', f'{commit_hash}^']
    output = run_git_command(cmd, repo_path)
    return [output] if output else []


def get_merge_diff(repo_path: str, merge_hash: str, max_lines: int = 500) -> str:
    """Get the diff for a merge commit, limited to max_lines to avoid overwhelming the LLM."""
    try:
        # Get the diff for the merge commit
        cmd = ['show', '--format=', '--no-merges', merge_hash]
        diff_output = run_git_command(cmd, repo_path)
        
        if not diff_output:
            return ""
        
        # Limit the diff to max_lines to avoid token limits
        lines = diff_output.split('\n')
        if len(lines) > max_lines:
            truncated_lines = lines[:max_lines]
            truncated_lines.append(f"\n... (diff truncated after {max_lines} lines) ...")
            return '\n'.join(truncated_lines)
        
        return diff_output
    except Exception as e:
        return f"Error getting diff: {str(e)}"


def get_commit_range_details(repo_path: str, merge_commit: Dict, users: List[str]) -> Dict:
    """Get details about the commits in a merge (PR equivalent)."""
    # Check if merge commit author is in users list
    merge_author_match = merge_commit['author'] in users
    
    # Get full merge commit details including message body
    merge_details = get_commit_details(repo_path, merge_commit['hash'])
    merge_message = merge_details.get('body', '') if merge_details else ''
    # Combine subject and body for full message
    full_merge_message = merge_commit['subject']
    if merge_message.strip():
        full_merge_message += '\n\n' + merge_message.strip()
    
    # Handle squashed PR commits differently than traditional merges
    if not merge_commit.get('is_traditional_merge', True):
        # For squashed PRs, the commit itself is the PR
        if merge_author_match:
            details = get_commit_details(repo_path, merge_commit['hash'])
            return {
                'merge_hash': merge_commit['hash'],
                'merge_subject': merge_commit['subject'],
                'merge_message': full_merge_message,
                'merge_date': merge_commit['date'],
                'author': merge_commit['author'],
                'commits_count': 1,
                'first_commit_date': merge_commit['date'],
                'last_commit_date': merge_commit['date'],
                'development_hours': 0,
                'review_hours': 0,
                'additions': details.get('additions', 0),
                'deletions': details.get('deletions', 0),
                'files_changed': details.get('files_changed', 0),
                'description': merge_commit['subject'],
                'pr_commits': [{
                    'hash': merge_commit['hash'],
                    'author': merge_commit['author'],
                    'email': '',
                    'date': merge_commit['date'],
                    'subject': merge_commit['subject']
                }],
                'repo_path': repo_path
            }
        else:
            return {}
    
    # Get the parent commits of the merge commit
    parents = get_parent_commits(repo_path, merge_commit['hash'])
    if not parents:
        # If no parents but merge author matches, still include it
        if merge_author_match:
            return {
                'merge_hash': merge_commit['hash'],
                'merge_subject': merge_commit['subject'],
                'merge_message': full_merge_message,
                'merge_date': merge_commit['date'],
                'author': merge_commit['author'],
                'commits_count': 1,
                'first_commit_date': merge_commit['date'],
                'last_commit_date': merge_commit['date'],
                'development_hours': 0,
                'review_hours': 0,
                'additions': 0,
                'deletions': 0,
                'files_changed': 0,
                'description': merge_commit['subject'],
                'pr_commits': [],
                'repo_path': repo_path
            }
        return {}
    
    # Get commits between the first parent and the merge commit
    # This represents the "PR" commits
    base_commit = parents[0]
    cmd = ['log', '--pretty=format:%H|%an|%ae|%ad|%s', '--date=iso', f'{base_commit}..{merge_commit["hash"]}']
    output = run_git_command(cmd, repo_path)
    
    if not output:
        # If no PR commits but merge author matches, still include it
        if merge_author_match:
            return {
                'merge_hash': merge_commit['hash'],
                'merge_subject': merge_commit['subject'],
                'merge_message': full_merge_message,
                'merge_date': merge_commit['date'],
                'author': merge_commit['author'],
                'commits_count': 1,
                'first_commit_date': merge_commit['date'],
                'last_commit_date': merge_commit['date'],
                'development_hours': 0,
                'review_hours': 0,
                'additions': 0,
                'deletions': 0,
                'files_changed': 0,
                'description': merge_commit['subject'],
                'pr_commits': [],
                'repo_path': repo_path
            }
        return {}
    
    pr_commits = []
    user_commits = []
    
    for line in output.split('\n'):
        if not line.strip():
            continue
        
        parts = line.split('|')
        if len(parts) != 5:
            continue
        
        commit_hash, author, email, date_str, subject = parts
        
        try:
            commit_date = date_parser.parse(date_str)
        except:
            continue
        
        commit_info = {
            'hash': commit_hash,
            'author': author,
            'email': email,
            'date': commit_date,
            'subject': subject
        }
        
        pr_commits.append(commit_info)
        
        # Track commits from specified users
        if author in users:
            user_commits.append(commit_info)
    
    # Include this merge if either:
    # 1. The merge author is in the users list, OR
    # 2. Any of the PR commits are from specified users
    if not merge_author_match and not user_commits:
        return {}
    
    # Calculate metrics - use user commits if available, otherwise all PR commits
    relevant_commits = user_commits if user_commits else pr_commits
    
    if relevant_commits:
        first_commit = min(relevant_commits, key=lambda x: x['date'])
        last_commit = max(relevant_commits, key=lambda x: x['date'])
        
        dev_time = (last_commit['date'] - first_commit['date']).total_seconds() / 3600
        review_time = (merge_commit['date'] - last_commit['date']).total_seconds() / 3600
        
        # Determine the primary author
        primary_author = first_commit['author']
    else:
        # Fallback to merge commit info
        first_commit = last_commit = merge_commit
        dev_time = review_time = 0
        primary_author = merge_commit['author']
    
    # Get total changes from all PR commits (not just user commits)
    total_additions = 0
    total_deletions = 0
    total_files = 0
    
    for commit in pr_commits:
        details = get_commit_details(repo_path, commit['hash'])
        total_additions += details.get('additions', 0)
        total_deletions += details.get('deletions', 0)
        total_files = max(total_files, details.get('files_changed', 0))
    
    return {
        'merge_hash': merge_commit['hash'],
        'merge_subject': merge_commit['subject'],
        'merge_message': full_merge_message,
        'merge_date': merge_commit['date'],
        'author': primary_author,
        'commits_count': len(relevant_commits),
        'first_commit_date': first_commit['date'],
        'last_commit_date': last_commit['date'],
        'development_hours': round(dev_time, 2),
        'review_hours': round(review_time, 2),
        'additions': total_additions,
        'deletions': total_deletions,
        'files_changed': total_files,
        'description': merge_commit['subject'],
        'pr_commits': relevant_commits,
        'repo_path': repo_path  # Add repo path so we can get diff later
    }


def clear_llm_cache():
    """Clear the LLM response cache."""
    if os.path.exists(CACHE_DB_PATH):
        conn = sqlite3.connect(CACHE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM llm_cache')
        conn.commit()
        conn.close()
        click.echo("Cache cleared successfully.")
    else:
        click.echo("Cache database does not exist.")


def get_cache_stats():
    """Get cache statistics."""
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM llm_cache")
    total_entries = cursor.fetchone()[0]
    
    cursor.execute("SELECT model, COUNT(*) FROM llm_cache GROUP BY model")
    by_model = cursor.fetchall()
    
    conn.close()
    
    return {
        'total_entries': total_entries,
        'by_model': {model: count for model, count in by_model}
    }


def get_all_users_from_repo(repo_path: str, since_date: datetime) -> List[str]:
    """Get all unique users who have commits in the repository since the given date."""
    try:
        # Get all authors since the date
        cmd = [
            "log", "--format=%an", "--since", since_date.strftime("%Y-%m-%d"),
            "--no-merges"
        ]
        result = run_git_command(cmd, repo_path)
        
        # Get unique authors
        authors = set()
        for line in result.split('\n'):
            line = line.strip()
            if line:
                authors.add(line)
        
        return sorted(list(authors))
    except Exception as e:
        click.echo(f"Error getting users from repository: {e}", err=True)
        return []


@click.command()
@click.option('--since', help='Timestamp or relative time (e.g., "1 week ago")')
@click.option('--repo', envvar='REPO_PATH', help='Path to local git repository (or set REPO_PATH environment variable)')

@click.option('--openai-key', envvar='OPENAI_API_KEY', help='OpenAI API key (or set OPENAI_API_KEY environment variable or use .env file)')
@click.option('--model', default='gpt-4', help='OpenAI model to use (gpt-4 or gpt-3.5-turbo)')
@click.option('--include-diff', is_flag=True, help='Include code diff in LLM analysis (may increase API costs)')
@click.option('--clear-cache', is_flag=True, help='Clear the LLM response cache before running')
@click.option('--cache-stats', is_flag=True, help='Show cache statistics and exit')
def main(since: str, repo: str, openai_key: str, model: str, include_diff: bool, clear_cache: bool, cache_stats: bool):
    """Analyze local git repository merges and score them based on AI Measurement Framework."""
    
    # Handle cache operations
    if cache_stats:
        stats = get_cache_stats()
        click.echo(f"Cache Statistics:")
        click.echo(f"  Total entries: {stats['total_entries']}")
        click.echo(f"  Models used: {', '.join(stats['by_model'].keys()) if stats['by_model'] else 'None'}")
        return
    
    if clear_cache:
        clear_llm_cache()
    
    # Validate required inputs for analysis
    if not since:
        click.echo("Error: --since is required for analysis", err=True)
        sys.exit(1)
    
    # Parse dates first
    try:
        since_date = parse_relative_date(since)
        click.echo(f"Analyzing merges since: {since_date.strftime('%Y-%m-%d %H:%M:%S')}")
    except ValueError as e:
        click.echo(f"Error parsing date: {e}", err=True)
        sys.exit(1)
    
    # Get all users from the repository
    user_list = get_all_users_from_repo(repo, since_date)
    if not user_list:
        click.echo("No users found in repository for the specified date range.", err=True)
        sys.exit(1)
    
    if not repo:
        click.echo("Error: --repo is required for analysis", err=True)
        sys.exit(1)
    
    if not openai_key:
        click.echo("Error: OpenAI API key not provided. Set OPENAI_API_KEY environment variable or use --openai-key", err=True)
        sys.exit(1)
    
    if not os.path.isdir(repo):
        click.echo(f"Error: Repository path does not exist: {repo}", err=True)
        sys.exit(1)
    
    # Check if it's a git repository
    if not os.path.isdir(os.path.join(repo, '.git')):
        click.echo(f"Error: Not a git repository: {repo}", err=True)
        sys.exit(1)
    
    click.echo(f"Analyzing merges for all users: {', '.join(user_list)}")
    click.echo(f"Repository: {repo}")
    
    # Initialize OpenAI client
    client = OpenAI(api_key=openai_key)
    
    # Initialize databases
    init_cache_db()
    init_analysis_db()
    
    # Show cache stats
    cache_info = get_cache_stats()
    click.echo(f"Cache: {cache_info['total_entries']} cached responses available")
    
    # Get merge commits
    click.echo("\nFetching merge commits...")
    merge_commits = get_merge_commits(repo, since_date)
    
    if not merge_commits:
        click.echo("No merge commits found since the specified date.")
        return
    
    click.echo(f"Found {len(merge_commits)} merge commits.")
    
    # Analyze merges that involve our users
    click.echo("\nAnalyzing merges...")
    pr_data = []
    
    for merge in merge_commits:
        click.echo(f"  Analyzing merge: {merge['subject'][:60]}...")
        
        merge_details = get_commit_range_details(repo, merge, user_list)
        if merge_details:
            pr_data.append(merge_details)
            click.echo(f"    Found {merge_details['commits_count']} commits by {merge_details['author']}")
    
    if not pr_data:
        click.echo("No merges found involving the specified users.")
        return
    
    # Filter revert chains before scoring
    click.echo(f"\nFiltering revert chains...")
    filtered_pr_data = filter_revert_chains(pr_data)
    click.echo(f"Filtered {len(pr_data)} → {len(filtered_pr_data)} merges (removed {len(pr_data) - len(filtered_pr_data)} reverted commits)")
    
    if not filtered_pr_data:
        click.echo("No merges remaining after filtering revert chains.")
        return
    
    # Score PRs using LLM
    click.echo(f"\nAnalyzing merges with AI (using {model})...")
    if include_diff:
        click.echo("  Including code diffs in analysis...")
    scored_prs = score_prs(filtered_pr_data, client, model, include_diff)
    
    # Save to database
    click.echo(f"\nSaving results to database...")
    save_analysis_to_db(scored_prs)
    
    click.echo(f"Analysis complete! Results saved to database. Run 'python3 web_app.py' to view results.")


def score_prs(pr_data: List[Dict], client: OpenAI, model: str = "gpt-4", include_diff: bool = False) -> List[Dict]:
    """Score PRs using OpenAI API."""
    scored_prs = []
    
    for pr in pr_data:
        # Skip LLM scoring if impact score is already assigned (e.g., from revert chain analysis)
        if 'impact_score' in pr and pr['impact_score'] == 0:
            click.echo(f"  Skipping LLM scoring for revert chain commit: {pr['merge_subject'][:60]}...")
            scored_prs.append(pr)
            continue
            
        click.echo(f"  Scoring merge: {pr['merge_subject'][:60]}...")
        
        # Prepare context for LLM
        context = f"""
Merge Analysis:
- Subject: {pr['merge_subject']}
- Author: {pr['author']}
- Commits: {pr['commits_count']}
- Lines Added: {pr['additions']}
- Lines Deleted: {pr['deletions']}
- Files Changed: {pr['files_changed']}
- Development Time: {pr['development_hours']} hours
- Review Time: {pr['review_hours']} hours
- Merge Date: {pr['merge_date']}
"""
        
        # Add commit details
        commit_details = []
        for commit in pr['pr_commits']:
            commit_details.append(f"- {commit['subject']} ({commit['date'].strftime('%Y-%m-%d %H:%M')})")
        
        if commit_details:
            context += "\nCommits:\n" + "\n".join(commit_details)
        
        # Add merge message if available
        if pr.get('merge_message') and pr['merge_message'] != pr['merge_subject']:
            context += f"\n\nFull Commit Message:\n{pr['merge_message']}"
        
        # Add diff content if requested and available
        if include_diff and pr.get('repo_path'):
            diff_content = get_merge_diff(pr['repo_path'], pr['merge_hash'])
            if diff_content and diff_content.strip():
                context += f"\n\nCode Changes (Diff):\n```diff\n{diff_content}\n```"
        
        # Create the full prompt for caching
        full_prompt = f"{SCORING_CRITERIA}\n\n{context}\n\nProvide the analysis in the exact JSON format specified above."
        
        # Check cache first
        cached_response = get_cached_response(full_prompt, model)
        if cached_response:
            click.echo(f"    Using cached response for merge: {pr['merge_subject'][:60]}...")
            content = cached_response
        else:
            # Call OpenAI API
            click.echo(f"    Calling OpenAI API for merge: {pr['merge_subject'][:60]}...")
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are an expert at analyzing software engineering merges and scoring them based on the DX AI Measurement Framework."},
                        {"role": "user", "content": full_prompt}
                    ],
                    temperature=0.3,
                    max_tokens=1000
                )
                
                # Parse response
                content = response.choices[0].message.content
                
                # Cache the response
                if content:
                    cache_response(full_prompt, model, content)
            except Exception as e:
                click.echo(f"    Error calling OpenAI API: {e}", err=True)
                content = None
        
        try:
            if content:
                # Clean up markdown formatting if present
                cleaned_content = content.strip()
                if cleaned_content.startswith('```json'):
                    # Remove markdown code block formatting
                    cleaned_content = cleaned_content[7:]  # Remove ```json
                    # Find the end of the JSON block
                    end_marker = cleaned_content.find('```')
                    if end_marker != -1:
                        cleaned_content = cleaned_content[:end_marker]
                elif cleaned_content.endswith('```'):
                    cleaned_content = cleaned_content[:-3]  # Remove ```
                cleaned_content = cleaned_content.strip()
                
                scores = json.loads(cleaned_content)
            else:
                raise ValueError("Empty response from OpenAI API")
            
            # Add scores to PR data
            pr.update(scores)
            
        except Exception as e:
            click.echo(f"    Error scoring merge: {e}", err=True)
            # Add default scores on error
            pr.update({
                'impact_score': 1,
                'impact_assessment': f'Error during analysis: {str(e)}'
            })
        
        scored_prs.append(pr)
    
    return scored_prs


def extract_original_subject(subject: str) -> Tuple[str, bool]:
    """
    Recursively extract the original subject from nested revert titles.
    Returns (original_subject, is_revert)
    """
    import re
    
    current_subject = subject
    is_revert = False
    
    while True:
        matched = False
        
        # Try to match "Revert:" format first
        if re.match(r"^[Rr]evert:", current_subject):
            # Extract what comes after "Revert:"
            after_colon = current_subject[7:]  # Skip "Revert:"
            current_subject = after_colon.strip()
            is_revert = True
            matched = True
            continue
        
        # Try to match "Revert " followed by quoted content (handling nested quotes)
        elif re.match(r"^[Rr]evert ", current_subject):
            after_revert = current_subject[7:]  # Skip "Revert "
            
            # Handle double quotes - find the last matching quote
            if after_revert.startswith('"'):
                # Find the last quote that would close the outermost quote
                last_quote_pos = after_revert.rfind('"')
                if last_quote_pos > 0:  # Make sure there's a closing quote
                    extracted = after_revert[1:last_quote_pos]
                    current_subject = extracted
                    is_revert = True
                    matched = True
                    continue
            
            # Handle single quotes - find the last matching quote
            elif after_revert.startswith("'"):
                last_quote_pos = after_revert.rfind("'")
                if last_quote_pos > 0:  # Make sure there's a closing quote
                    extracted = after_revert[1:last_quote_pos]
                    current_subject = extracted
                    is_revert = True
                    matched = True
                    continue
            
            # Handle "Revert Title" format (no quotes)
            else:
                current_subject = after_revert
                is_revert = True
                matched = True
                continue
        
        if not matched:
            break
    
    return current_subject, is_revert


def filter_revert_chains(pr_data: List[Dict]) -> List[Dict]:
    """Analyze revert chains and assign impact scores appropriately."""
    import re
    
    # Group commits by normalized subject
    commits_by_subject = {}
    revert_info = {}
    
    for pr in pr_data:
        subject = pr['merge_subject']
        
        # Use recursive parser to extract original subject
        original_subject, is_revert = extract_original_subject(subject)
        
        # Normalize subjects to handle PR numbers - strip PR number for grouping
        import re
        normalized_subject = re.sub(r'\s*\(#\d+\)$', '', original_subject)
        
        # Debug logging for recursive parsing
        if subject != original_subject:
            click.echo(f"  Parsed: '{subject}' → original: '{original_subject}' → normalized: '{normalized_subject}' (revert: {is_revert})")
        
        # Track commits by their normalized subject for grouping
        if normalized_subject not in commits_by_subject:
            commits_by_subject[normalized_subject] = []
        
        commits_by_subject[normalized_subject].append({
            'pr': pr,
            'is_revert': is_revert,
            'original_subject': original_subject,
            'normalized_subject': normalized_subject,
            'merge_date': pr['merge_date']
        })
    
    # For each subject group, determine impact scores
    all_prs = []
    
    for subject, commits in commits_by_subject.items():
        if len(commits) == 1:
            # Single commit, keep it with normal scoring
            all_prs.append(commits[0]['pr'])
        else:
            # Multiple commits with same subject - analyze the chain
            # Sort by merge date to understand the sequence
            commits.sort(key=lambda x: x['merge_date'])
            
            # Track the state: True = change is active, False = change is reverted
            # Start with False (no change is active initially)
            state = False
            final_commit = None
            
            for commit in commits:
                old_state = state
                if commit['is_revert']:
                    state = not state  # Revert flips the state
                else:
                    state = True  # Original change makes it active
                
                # Always update final_commit to the current commit when state becomes True
                # This ensures we track the last commit that made the change active
                final_commit = commit if state else None
            
            # Assign impact scores based on position in chain
            for commit in commits:
                if commit == final_commit:
                    # This is the final commit that represents the actual change
                    # It will get normal LLM scoring
                    all_prs.append(commit['pr'])
                else:
                    # This commit is part of a revert chain but not the final state
                    # Assign impact score 0 and skip LLM scoring
                    pr_copy = commit['pr'].copy()
                    pr_copy['impact_score'] = 0
                    pr_copy['impact_assessment'] = 'Part of revert chain - no net impact'
                    all_prs.append(pr_copy)
            
            # Log the chain for debugging
            chain_summary = " → ".join([
                f"{'R:' if c['is_revert'] else 'O:'}{c['pr']['merge_subject'][:50]}"
                for c in commits
            ])
            final_summary = f"FINAL: {final_commit['pr']['merge_subject'][:50]}" if final_commit else "NO FINAL"
            click.echo(f"  Revert chain for '{subject}': {chain_summary} → {final_summary}")
    
    return all_prs


if __name__ == '__main__':
    main()
