#!/usr/bin/env python3
"""
Web App for eng ctx
Displays PR analysis results from SQLite database
"""

import sqlite3
import os
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from typing import List, Dict, Optional

# Impact Scoring Configuration
# Change these weights to adjust how impact scores are calculated
# Default: Fibonacci sequence for exponential impact weighting
IMPACT_WEIGHTS = {
    1: 1,    # Very Low Impact
    2: 2,    # Low Impact  
    3: 5,    # Medium Impact
    4: 13,   # High Impact
    5: 21    # Very High Impact
}

def calculate_impact_points(score: int) -> int:
    """Convert impact score to impact points using configurable weights."""
    return IMPACT_WEIGHTS.get(score, 1)


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
        error_msg = e.stderr.strip() if e.stderr else "Unknown error"
        return f"Git command failed: git {' '.join(cmd)}\nError: {error_msg}\nRepository: {cwd}"


def get_merge_diff(repo_path: str, merge_hash: str, max_lines: int = 2000) -> str:
    """Get the diff for a merge commit."""
    try:
        # Get the diff for the merge commit
        cmd = ['show', '--format=', '--no-merges', merge_hash]
        diff_output = run_git_command(cmd, repo_path)
        
        if not diff_output:
            return f"No diff found for commit {merge_hash[:8]}. This commit may not exist in the repository at {repo_path}."
        
        # Limit the diff to max_lines to avoid overwhelming the browser
        lines = diff_output.split('\n')
        if len(lines) > max_lines:
            truncated_lines = lines[:max_lines]
            truncated_lines.append(f"\n... (diff truncated after {max_lines} lines) ...")
            return '\n'.join(truncated_lines)
        
        return diff_output
    except Exception as e:
        return f"Error getting diff for commit {merge_hash[:8]}: {str(e)}\nRepository path: {repo_path}"

def calculate_total_impact_points(scores: List[int]) -> int:
    """Calculate total impact points from a list of scores."""
    return sum(calculate_impact_points(score) for score in scores)

def calculate_score_distribution(scores: List[int]) -> Dict[int, Dict]:
    """Calculate distribution of scores with counts and percentages."""
    if not scores:
        return {}
    
    total = len(scores)
    distribution = {}
    
    for score in range(1, 6):
        count = scores.count(score)
        percentage = (count / total) * 100 if total > 0 else 0
        distribution[score] = {
            'count': count,
            'percentage': round(percentage, 1),
            'points': count * calculate_impact_points(score)
        }
    
    return distribution

app = Flask(__name__)

# Database configuration
ANALYSIS_DB_PATH = "db/pr_analysis.db"

# Repository configuration - set this to the path where your analyzed repository is located
DEFAULT_REPO_PATH = os.environ.get('REPO_PATH', '.')

def get_db_connection():
    """Get database connection."""
    if not os.path.exists(ANALYSIS_DB_PATH):
        return None
    return sqlite3.connect(ANALYSIS_DB_PATH)

def get_date_range_sql(time_filter: Optional[str]) -> tuple[str, list]:
    """Get SQL condition and parameters for date filtering."""
    if not time_filter:
        return "", []
    
    now = datetime.now()
    
    if time_filter == "last_week":
        start_date = now - timedelta(days=7)
        condition = " AND datetime(merge_date) >= datetime(?)"
        return condition, [start_date.isoformat()]
    elif time_filter == "last_month": 
        start_date = now - timedelta(days=30)
        condition = " AND datetime(merge_date) >= datetime(?)"
        return condition, [start_date.isoformat()]
    
    return "", []

def get_all_analyses(author_filter: Optional[str] = None, sort_by: str = "impact_score", sort_order: str = "desc", time_filter: Optional[str] = None) -> List[Dict]:
    """Get all PR analyses from database."""
    conn = get_db_connection()
    if not conn:
        return []
    
    cursor = conn.cursor()
    
    # Build query (excluding revert chain commits)
    query = "SELECT * FROM pr_analysis WHERE impact_score > 0"
    params = []
    
    if author_filter:
        query += " AND author = ?"
        params.append(author_filter)
    
    # Add date filtering
    date_condition, date_params = get_date_range_sql(time_filter)
    query += date_condition
    params.extend(date_params)
    
    # Add sorting
    if sort_by in ['impact_score', 'merge_date', 'additions', 'deletions', 'author']:
        query += f" ORDER BY {sort_by}"
        if sort_order.lower() == "desc":
            query += " DESC"
        else:
            query += " ASC"
    
    cursor.execute(query, params)
    columns = [description[0] for description in cursor.description]
    results = []
    
    for row in cursor.fetchall():
        result = dict(zip(columns, row))
        # Convert date strings to datetime objects for better formatting
        if result['merge_date']:
            try:
                result['merge_date'] = datetime.fromisoformat(result['merge_date'].replace('Z', '+00:00'))
            except:
                pass
        if result['analyzed_at']:
            try:
                result['analyzed_at'] = datetime.fromisoformat(result['analyzed_at'].replace('Z', '+00:00'))
            except:
                pass
        results.append(result)
    
    conn.close()
    return results

def get_summary_stats(time_filter: Optional[str] = None) -> Dict:
    """Get summary statistics."""
    conn = get_db_connection()
    if not conn:
        return {}

    cursor = conn.cursor()
    
    # Add date filtering to base query
    base_where = "WHERE impact_score > 0"
    date_condition, date_params = get_date_range_sql(time_filter)
    base_where += date_condition
    
    # Get all scores for overall stats (excluding revert chain commits)
    cursor.execute(f"SELECT impact_score FROM pr_analysis {base_where}", date_params)
    all_scores = [row[0] for row in cursor.fetchall()]
    
    # Get overall stats (excluding revert chain commits)
    cursor.execute(f"""
        SELECT 
            COUNT(*) as total_analyses,
            SUM(additions) as total_additions,
            SUM(deletions) as total_deletions
        FROM pr_analysis
        {base_where}
    """, date_params)
    
    overall_stats = cursor.fetchone()
    
    # Calculate overall impact points and distribution
    total_impact_points = calculate_total_impact_points(all_scores)
    overall_distribution = calculate_score_distribution(all_scores)
    
    # Get stats by author (excluding revert chain commits)
    cursor.execute(f"""
        SELECT 
            author,
            COUNT(*) as merge_count,
            SUM(additions) as total_additions,
            SUM(deletions) as total_deletions,
            SUM(files_changed) as total_files_changed
        FROM pr_analysis 
        {base_where}
        GROUP BY author
    """, date_params)
    
    author_results = cursor.fetchall()
    
    # Calculate impact points for each author
    author_stats = []
    for row in author_results:
        author, merge_count, total_additions, total_deletions, total_files_changed = row
        
        # Get individual scores for this author (excluding revert chain commits)
        author_where = f"{base_where} AND author = ?"
        author_params = date_params + [author]
        cursor.execute(f"SELECT impact_score FROM pr_analysis {author_where}", author_params)
        author_scores = [row[0] for row in cursor.fetchall()]
        
        author_impact_points = calculate_total_impact_points(author_scores)
        author_distribution = calculate_score_distribution(author_scores)
        
        author_stats.append({
            'author': author,
            'merge_count': merge_count,
            'impact_points': author_impact_points,
            'distribution': author_distribution,
            'total_additions': total_additions,
            'total_deletions': total_deletions,
            'total_files_changed': total_files_changed
        })
    
    # Sort by impact points (descending)
    author_stats.sort(key=lambda x: x['impact_points'], reverse=True)
    
    conn.close()
    
    return {
        'overall': {
            'total_analyses': overall_stats[0] or 0,
            'impact_points': total_impact_points,
            'distribution': overall_distribution,
            'total_additions': overall_stats[1] or 0,
            'total_deletions': overall_stats[2] or 0
        },
        'by_author': author_stats
    }

def get_unique_authors() -> List[str]:
    """Get list of unique authors (excluding revert chain commits)."""
    conn = get_db_connection()
    if not conn:
        return []
    
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT author FROM pr_analysis WHERE impact_score > 0 ORDER BY author")
    authors = [row[0] for row in cursor.fetchall()]
    conn.close()
    return authors

def get_analysis_by_hash(merge_hash: str) -> Optional[Dict]:
    """Get single analysis by merge hash."""
    conn = get_db_connection()
    if not conn:
        return None
    
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pr_analysis WHERE merge_hash = ?", (merge_hash,))
    columns = [description[0] for description in cursor.description]
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return None
    
    result = dict(zip(columns, row))
    # Convert date strings to datetime objects for better formatting
    if result['merge_date']:
        try:
            result['merge_date'] = datetime.fromisoformat(result['merge_date'].replace('Z', '+00:00'))
        except:
            pass
    if result['analyzed_at']:
        try:
            result['analyzed_at'] = datetime.fromisoformat(result['analyzed_at'].replace('Z', '+00:00'))
        except:
            pass
    
    conn.close()
    return result

@app.route('/')
def index():
    """Main dashboard page."""
    # Get filter parameters
    author_filter = request.args.get('author')
    time_filter = request.args.get('time_filter')
    sort_by = request.args.get('sort_by', 'impact_score')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Get data
    analyses = get_all_analyses(author_filter, sort_by, sort_order, time_filter)
    stats = get_summary_stats(time_filter)
    authors = get_unique_authors()
    
    if not analyses:
        return render_template('no_data.html')
    
    return render_template('dashboard.html', 
                         analyses=analyses, 
                         stats=stats, 
                         authors=stats['by_author'],
                         current_author=author_filter,
                         current_time_filter=time_filter,
                         current_sort=sort_by,
                         current_order=sort_order)

@app.route('/commit/<merge_hash>')
def commit_detail(merge_hash: str):
    """Detailed commit view page."""
    # Get filter parameters to preserve them in back link
    author_filter = request.args.get('author')
    time_filter = request.args.get('time_filter')
    
    analysis = get_analysis_by_hash(merge_hash)
    
    if not analysis:
        return "Commit not found", 404
    
    # Get the full diff if repo_path is available
    diff_content = ""
    repo_path = analysis.get('repo_path')
    
    # If repo_path is not stored, use the default repository path
    if not repo_path:
        repo_path = DEFAULT_REPO_PATH
    
    if repo_path:
        diff_content = get_merge_diff(repo_path, merge_hash)
    
    return render_template('commit_detail.html', 
                         analysis=analysis, 
                         diff_content=diff_content,
                         current_author=author_filter,
                         current_time_filter=time_filter)




@app.route('/api/analyses')
def api_analyses():
    """API endpoint for analyses data."""
    author_filter = request.args.get('author')
    time_filter = request.args.get('time_filter')
    sort_by = request.args.get('sort_by', 'impact_score')
    sort_order = request.args.get('sort_order', 'desc')
    
    analyses = get_all_analyses(author_filter, sort_by, sort_order, time_filter)
    
    # Convert datetime objects to strings for JSON serialization
    for analysis in analyses:
        if isinstance(analysis.get('merge_date'), datetime):
            analysis['merge_date'] = analysis['merge_date'].strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(analysis.get('analyzed_at'), datetime):
            analysis['analyzed_at'] = analysis['analyzed_at'].strftime('%Y-%m-%d %H:%M:%S')
    
    return jsonify(analyses)

@app.route('/api/stats')
def api_stats():
    """API endpoint for summary statistics."""
    time_filter = request.args.get('time_filter')
    return jsonify(get_summary_stats(time_filter))

if __name__ == '__main__':
    if not os.path.exists(ANALYSIS_DB_PATH):
        print(f"Database not found: {ANALYSIS_DB_PATH}")
        print("Run 'python3 analyze_prs.py' first to generate analysis data.")
        exit(1)
    
    print("Starting eng ctx Web App...")
    print("Open http://localhost:8081 in your browser")
    app.run(debug=True, host='0.0.0.0', port=8081) 