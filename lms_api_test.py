from flask import Flask, jsonify, request
from flask_cors import CORS
import pyodbc
import pandas as pd
from datetime import datetime, timedelta
import json
from functools import wraps

app = Flask(__name__)
CORS(app)  # Enable CORS for Power BI

# Database Configuration for Windows Authentication
DATABASE_CONFIG = {
    'server': 'JONESFAMILYPC3',  # or your server name like 'DESKTOP-ABC123\\SQLEXPRESS'
    'database': 'EDU_LMS_STAGE',
    'driver': 'ODBC Driver 17 for SQL Server',
    'trusted_connection': 'yes'  # Windows Authentication
}

def get_db_connection():
    """Create a database connection using Windows Authentication"""
    conn_str = (
        f"DRIVER={{{DATABASE_CONFIG['driver']}}};"
        f"SERVER={DATABASE_CONFIG['server']};"
        f"DATABASE={DATABASE_CONFIG['database']};"
        f"Trusted_Connection={DATABASE_CONFIG['trusted_connection']};"
    )
    return pyodbc.connect(conn_str)

def query_to_json(query, params=None):
    """Execute query and return results as JSON"""
    try:
        conn = get_db_connection()
        if params:
            df = pd.read_sql(query, conn, params=params)
        else:
            df = pd.read_sql(query, conn)
        conn.close()
        
        # Convert datetime columns to string, but handle NaT properly
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S') if pd.notnull(x) else None)
        
        # Replace NaN and NaT with None for proper JSON null handling
        df = df.replace({pd.NA: None, pd.NaT: None, float('nan'): None, 'NaT': None})
        df = df.where(pd.notnull(df), None)
        
        return df.to_dict(orient='records')
    except Exception as e:
        return {'error': str(e)}
# Error handler decorator
def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return decorated_function

# ============================================================================
# MAIN ANALYTICS ENDPOINTS FOR POWER BI
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint to verify API and database connectivity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/dashboard/summary', methods=['GET'])
@handle_errors
def dashboard_summary():
    """
    Get overall LMS statistics for Power BI dashboard
    Returns: Total users, courses, enrollments, completion rate
    """
    query = """
    SELECT 
        (SELECT COUNT(*) FROM Users WHERE is_active = 1) as total_users,
        (SELECT COUNT(*) FROM Users WHERE role = 'student' AND is_active = 1) as total_students,
        (SELECT COUNT(*) FROM Users WHERE role = 'instructor' AND is_active = 1) as total_instructors,
        (SELECT COUNT(*) FROM Courses WHERE is_active = 1) as total_courses,
        (SELECT COUNT(*) FROM Enrollments) as total_enrollments,
        (SELECT COUNT(*) FROM Enrollments WHERE status = 'completed') as completed_enrollments,
        (SELECT COUNT(*) FROM Enrollments WHERE status = 'active') as active_enrollments,
        CASE 
            WHEN (SELECT COUNT(*) FROM Enrollments) > 0 
            THEN CAST((SELECT COUNT(*) FROM Enrollments WHERE status = 'completed') AS FLOAT) / 
                 CAST((SELECT COUNT(*) FROM Enrollments) AS FLOAT) * 100
            ELSE 0 
        END as completion_rate
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/courses', methods=['GET'])
@handle_errors
def get_courses():
    """
    Get all courses with enrollment statistics
    Power BI Use: Course performance analysis, enrollment trends
    """
    query = """
    SELECT 
        c.course_id,
        c.course_name,
        c.course_code,
        c.category,
        c.difficulty_level,
        c.duration_hours,
        c.created_at,
        u.first_name + ' ' + u.last_name as instructor_name,
        COUNT(DISTINCT e.enrollment_id) as total_enrollments,
        COUNT(DISTINCT CASE WHEN e.status = 'completed' THEN e.enrollment_id END) as completed_count,
        COUNT(DISTINCT CASE WHEN e.status = 'active' THEN e.enrollment_id END) as active_count,
        AVG(e.progress_percentage) as avg_progress,
        CASE 
            WHEN COUNT(DISTINCT e.enrollment_id) > 0 
            THEN CAST(COUNT(DISTINCT CASE WHEN e.status = 'completed' THEN e.enrollment_id END) AS FLOAT) / 
                 CAST(COUNT(DISTINCT e.enrollment_id) AS FLOAT) * 100
            ELSE 0 
        END as completion_rate
    FROM Courses c
    LEFT JOIN Users u ON c.instructor_id = u.user_id
    LEFT JOIN Enrollments e ON c.course_id = e.course_id
    WHERE c.is_active = 1
    GROUP BY 
        c.course_id, c.course_name, c.course_code, c.category, 
        c.difficulty_level, c.duration_hours, c.created_at,
        u.first_name, u.last_name
    ORDER BY c.course_name
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/courses/<int:course_id>', methods=['GET'])
@handle_errors
def get_course_detail(course_id):
    """Get detailed information about a specific course"""
    query = """
    SELECT 
        c.course_id,
        c.course_name,
        c.course_code,
        c.description,
        c.category,
        c.difficulty_level,
        c.duration_hours,
        c.created_at,
        u.first_name + ' ' + u.last_name as instructor_name,
        u.email as instructor_email,
        COUNT(DISTINCT m.module_id) as total_modules,
        COUNT(DISTINCT l.lesson_id) as total_lessons,
        COUNT(DISTINCT e.enrollment_id) as total_enrollments
    FROM Courses c
    LEFT JOIN Users u ON c.instructor_id = u.user_id
    LEFT JOIN Modules m ON c.course_id = m.course_id
    LEFT JOIN Lessons l ON m.module_id = l.module_id
    LEFT JOIN Enrollments e ON c.course_id = e.course_id
    WHERE c.course_id = ?
    GROUP BY 
        c.course_id, c.course_name, c.course_code, c.description,
        c.category, c.difficulty_level, c.duration_hours, c.created_at,
        u.first_name, u.last_name, u.email
    """
    data = query_to_json(query, params=[course_id])
    return jsonify(data)

@app.route('/api/enrollments', methods=['GET'])
@handle_errors
def get_enrollments():
    """
    Get all enrollments with user and course details
    Power BI Use: Student progress tracking, enrollment trends over time
    """
    query = """
    SELECT 
        e.enrollment_id,
        e.user_id,
        u.username,
        u.first_name + ' ' + u.last_name as student_name,
        u.email as student_email,
        e.course_id,
        c.course_name,
        c.category,
        c.difficulty_level,
        e.enrollment_date,
        e.completion_date,
        e.progress_percentage,
        e.status,
        DATEDIFF(day, e.enrollment_date, GETDATE()) as days_enrolled,
        CASE 
            WHEN e.completion_date IS NOT NULL 
            THEN DATEDIFF(day, e.enrollment_date, e.completion_date)
            ELSE NULL 
        END as days_to_complete
    FROM Enrollments e
    JOIN Users u ON e.user_id = u.user_id
    JOIN Courses c ON e.course_id = c.course_id
    ORDER BY e.enrollment_date DESC
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/enrollments/trends', methods=['GET'])
@handle_errors
def enrollment_trends():
    """
    Get enrollment trends by month and category
    Power BI Use: Time series analysis, trend visualization
    """
    query = """
    SELECT 
        YEAR(e.enrollment_date) as enrollment_year,
        MONTH(e.enrollment_date) as enrollment_month,
        FORMAT(e.enrollment_date, 'yyyy-MM') as year_month,
        c.category,
        COUNT(e.enrollment_id) as enrollment_count,
        COUNT(CASE WHEN e.status = 'completed' THEN e.enrollment_id END) as completed_count,
        AVG(e.progress_percentage) as avg_progress
    FROM Enrollments e
    JOIN Courses c ON e.course_id = c.course_id
    GROUP BY 
        YEAR(e.enrollment_date),
        MONTH(e.enrollment_date),
        FORMAT(e.enrollment_date, 'yyyy-MM'),
        c.category
    ORDER BY enrollment_year, enrollment_month, c.category
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/students', methods=['GET'])
@handle_errors
def get_students():
    """
    Get all students with their enrollment statistics
    Power BI Use: Student performance analysis, engagement metrics
    """
    query = """
    SELECT 
        u.user_id,
        u.username,
        u.first_name + ' ' + u.last_name as student_name,
        u.email,
        u.created_at as registration_date,
        COUNT(DISTINCT e.enrollment_id) as total_enrollments,
        COUNT(DISTINCT CASE WHEN e.status = 'completed' THEN e.enrollment_id END) as completed_courses,
        COUNT(DISTINCT CASE WHEN e.status = 'active' THEN e.enrollment_id END) as active_courses,
        AVG(e.progress_percentage) as avg_progress,
        MAX(e.enrollment_date) as last_enrollment_date,
        DATEDIFF(day, MAX(e.enrollment_date), GETDATE()) as days_since_last_enrollment
    FROM Users u
    LEFT JOIN Enrollments e ON u.user_id = e.user_id
    WHERE u.role = 'student' AND u.is_active = 1
    GROUP BY 
        u.user_id, u.username, u.first_name, u.last_name, 
        u.email, u.created_at
    ORDER BY u.created_at DESC
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/students/<int:student_id>/progress', methods=['GET'])
@handle_errors
def get_student_progress(student_id):
    """Get detailed progress for a specific student"""
    query = """
    SELECT 
        e.enrollment_id,
        c.course_name,
        c.category,
        e.enrollment_date,
        e.progress_percentage,
        e.status,
        e.completion_date,
        COUNT(DISTINCT up.progress_id) as lessons_started,
        COUNT(DISTINCT CASE WHEN up.completed = 1 THEN up.progress_id END) as lessons_completed,
        SUM(up.time_spent_minutes) as total_time_spent_minutes,
        AVG(up.score) as avg_quiz_score
    FROM Enrollments e
    JOIN Courses c ON e.course_id = c.course_id
    LEFT JOIN UserProgress up ON e.user_id = up.user_id
    LEFT JOIN Lessons l ON up.lesson_id = l.lesson_id
    LEFT JOIN Modules m ON l.module_id = m.module_id
    WHERE e.user_id = ? AND m.course_id = c.course_id
    GROUP BY 
        e.enrollment_id, c.course_name, c.category,
        e.enrollment_date, e.progress_percentage, e.status, e.completion_date
    """
    data = query_to_json(query, params=[student_id])
    return jsonify(data)

@app.route('/api/progress/detailed', methods=['GET'])
@handle_errors
def get_detailed_progress():
    """
    Get detailed lesson-level progress for all students
    Power BI Use: Granular progress analysis, lesson difficulty assessment
    """
    query = """
    SELECT 
        u.user_id,
        u.first_name + ' ' + u.last_name as student_name,
        c.course_id,
        c.course_name,
        c.category,
        m.module_id,
        m.module_name,
        l.lesson_id,
        l.lesson_name,
        l.lesson_type,
        l.duration_minutes as lesson_duration,
        up.completed,
        up.completion_date,
        up.time_spent_minutes,
        up.score,
        CASE 
            WHEN up.completed = 1 AND l.duration_minutes > 0
            THEN CAST(up.time_spent_minutes AS FLOAT) / CAST(l.duration_minutes AS FLOAT) * 100
            ELSE NULL
        END as completion_efficiency
    FROM UserProgress up
    JOIN Users u ON up.user_id = u.user_id
    JOIN Lessons l ON up.lesson_id = l.lesson_id
    JOIN Modules m ON l.module_id = m.module_id
    JOIN Courses c ON m.course_id = c.course_id
    WHERE u.role = 'student'
    ORDER BY u.user_id, c.course_id, m.module_order, l.lesson_order
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/categories/performance', methods=['GET'])
@handle_errors
def category_performance():
    """
    Get performance metrics by course category
    Power BI Use: Category comparison, subject area analysis
    """
    query = """
    SELECT 
        c.category,
        COUNT(DISTINCT c.course_id) as total_courses,
        COUNT(DISTINCT e.enrollment_id) as total_enrollments,
        COUNT(DISTINCT e.user_id) as unique_students,
        AVG(e.progress_percentage) as avg_progress,
        COUNT(DISTINCT CASE WHEN e.status = 'completed' THEN e.enrollment_id END) as completed_enrollments,
        CASE 
            WHEN COUNT(DISTINCT e.enrollment_id) > 0 
            THEN CAST(COUNT(DISTINCT CASE WHEN e.status = 'completed' THEN e.enrollment_id END) AS FLOAT) / 
                 CAST(COUNT(DISTINCT e.enrollment_id) AS FLOAT) * 100
            ELSE 0 
        END as completion_rate,
        AVG(CAST(up.score AS FLOAT)) as avg_quiz_score,
        SUM(up.time_spent_minutes) as total_learning_minutes
    FROM Courses c
    LEFT JOIN Enrollments e ON c.course_id = e.course_id
    LEFT JOIN Modules m ON c.course_id = m.course_id
    LEFT JOIN Lessons l ON m.module_id = l.module_id
    LEFT JOIN UserProgress up ON l.lesson_id = up.lesson_id
    WHERE c.is_active = 1
    GROUP BY c.category
    ORDER BY c.category
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/quiz/performance', methods=['GET'])
@handle_errors
def quiz_performance():
    """
    Get quiz performance statistics
    Power BI Use: Assessment analysis, learning effectiveness
    """
    query = """
    SELECT 
        q.quiz_id,
        q.quiz_name,
        c.course_name,
        c.category,
        l.lesson_name,
        COUNT(DISTINCT uqa.attempt_id) as total_attempts,
        COUNT(DISTINCT uqa.user_id) as unique_students,
        AVG(CAST(uqa.score AS FLOAT)) as avg_score,
        AVG(CAST(uqa.total_points AS FLOAT)) as avg_total_points,
        COUNT(DISTINCT CASE WHEN uqa.passed = 1 THEN uqa.attempt_id END) as passed_attempts,
        CASE 
            WHEN COUNT(DISTINCT uqa.attempt_id) > 0 
            THEN CAST(COUNT(DISTINCT CASE WHEN uqa.passed = 1 THEN uqa.attempt_id END) AS FLOAT) / 
                 CAST(COUNT(DISTINCT uqa.attempt_id) AS FLOAT) * 100
            ELSE 0 
        END as pass_rate,
        AVG(DATEDIFF(minute, uqa.start_time, uqa.end_time)) as avg_duration_minutes
    FROM Quizzes q
    JOIN Lessons l ON q.lesson_id = l.lesson_id
    JOIN Modules m ON l.module_id = m.module_id
    JOIN Courses c ON m.course_id = c.course_id
    LEFT JOIN UserQuizAttempts uqa ON q.quiz_id = uqa.quiz_id
    WHERE q.is_active = 1
    GROUP BY 
        q.quiz_id, q.quiz_name, c.course_name, c.category, l.lesson_name
    ORDER BY c.course_name, l.lesson_name
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/engagement/metrics', methods=['GET'])
@handle_errors
def engagement_metrics():
    """
    Get student engagement metrics
    Power BI Use: Engagement analysis, retention insights
    """
    query = """
    SELECT 
        u.user_id,
        u.first_name + ' ' + u.last_name as student_name,
        COUNT(DISTINCT e.enrollment_id) as total_enrollments,
        SUM(up.time_spent_minutes) as total_learning_minutes,
        COUNT(DISTINCT up.lesson_id) as unique_lessons_accessed,
        COUNT(DISTINCT CASE WHEN up.completed = 1 THEN up.lesson_id END) as lessons_completed,
        MAX(up.updated_at) as last_activity_date,
        DATEDIFF(day, MAX(up.updated_at), GETDATE()) as days_since_last_activity,
        AVG(e.progress_percentage) as avg_course_progress,
        CASE 
            WHEN DATEDIFF(day, MAX(up.updated_at), GETDATE()) <= 7 THEN 'Active'
            WHEN DATEDIFF(day, MAX(up.updated_at), GETDATE()) <= 30 THEN 'Moderately Active'
            ELSE 'Inactive'
        END as engagement_status
    FROM Users u
    LEFT JOIN Enrollments e ON u.user_id = e.user_id
    LEFT JOIN UserProgress up ON u.user_id = up.user_id
    WHERE u.role = 'student' AND u.is_active = 1
    GROUP BY u.user_id, u.first_name, u.last_name
    HAVING COUNT(DISTINCT e.enrollment_id) > 0
    ORDER BY total_learning_minutes DESC
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/completion/funnel', methods=['GET'])
@handle_errors
def completion_funnel():
    """
    Get course completion funnel data
    Power BI Use: Funnel visualization, drop-off analysis
    """
    query = """
    WITH CourseFunnel AS (
        SELECT 
            c.course_id,
            c.course_name,
            c.category,
            COUNT(DISTINCT e.enrollment_id) as enrolled,
            COUNT(DISTINCT CASE WHEN e.progress_percentage > 0 THEN e.enrollment_id END) as started,
            COUNT(DISTINCT CASE WHEN e.progress_percentage >= 25 THEN e.enrollment_id END) as quarter_complete,
            COUNT(DISTINCT CASE WHEN e.progress_percentage >= 50 THEN e.enrollment_id END) as half_complete,
            COUNT(DISTINCT CASE WHEN e.progress_percentage >= 75 THEN e.enrollment_id END) as three_quarter_complete,
            COUNT(DISTINCT CASE WHEN e.status = 'completed' THEN e.enrollment_id END) as completed
        FROM Courses c
        LEFT JOIN Enrollments e ON c.course_id = e.course_id
        WHERE c.is_active = 1
        GROUP BY c.course_id, c.course_name, c.category
    )
    SELECT 
        *,
        CASE WHEN enrolled > 0 THEN CAST(started AS FLOAT) / enrolled * 100 ELSE 0 END as start_rate,
        CASE WHEN enrolled > 0 THEN CAST(completed AS FLOAT) / enrolled * 100 ELSE 0 END as completion_rate
    FROM CourseFunnel
    ORDER BY course_name
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/time/analysis', methods=['GET'])
@handle_errors
def time_analysis():
    """
    Get time-based learning analytics
    Power BI Use: Time series analysis, learning patterns
    """
    query = """
    SELECT 
        FORMAT(up.updated_at, 'yyyy-MM-dd') as date,
        FORMAT(up.updated_at, 'yyyy-MM') as year_month,
        DATEPART(WEEKDAY, up.updated_at) as day_of_week,
        DATEPART(HOUR, up.updated_at) as hour_of_day,
        c.category,
        COUNT(DISTINCT up.user_id) as active_students,
        COUNT(DISTINCT up.lesson_id) as lessons_accessed,
        SUM(up.time_spent_minutes) as total_minutes,
        COUNT(CASE WHEN up.completed = 1 THEN 1 END) as lessons_completed
    FROM UserProgress up
    JOIN Lessons l ON up.lesson_id = l.lesson_id
    JOIN Modules m ON l.module_id = m.module_id
    JOIN Courses c ON m.course_id = c.course_id
    WHERE up.updated_at >= DATEADD(month, -6, GETDATE())
    GROUP BY 
        FORMAT(up.updated_at, 'yyyy-MM-dd'),
        FORMAT(up.updated_at, 'yyyy-MM'),
        DATEPART(WEEKDAY, up.updated_at),
        DATEPART(HOUR, up.updated_at),
        c.category
    ORDER BY date DESC
    """
    data = query_to_json(query)
    return jsonify(data)

# ============================================================================
# FILTERED ENDPOINTS (WITH QUERY PARAMETERS)
# ============================================================================

@app.route('/api/enrollments/filter', methods=['GET'])
@handle_errors
def filter_enrollments():
    """
    Get filtered enrollments based on query parameters
    Supported filters: category, status, date_from, date_to
    """
    category = request.args.get('category')
    status = request.args.get('status')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    query = """
    SELECT 
        e.enrollment_id,
        u.first_name + ' ' + u.last_name as student_name,
        c.course_name,
        c.category,
        e.enrollment_date,
        e.progress_percentage,
        e.status
    FROM Enrollments e
    JOIN Users u ON e.user_id = u.user_id
    JOIN Courses c ON e.course_id = c.course_id
    WHERE 1=1
    """
    
    params = []
    if category:
        query += " AND c.category = ?"
        params.append(category)
    if status:
        query += " AND e.status = ?"
        params.append(status)
    if date_from:
        query += " AND e.enrollment_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND e.enrollment_date <= ?"
        params.append(date_to)
    
    query += " ORDER BY e.enrollment_date DESC"
    
    if params:
        data = query_to_json(query, params=params)
    else:
        data = query_to_json(query)
    
    return jsonify(data)

# ============================================================================
# METADATA ENDPOINTS
# ============================================================================

@app.route('/api/metadata/tables', methods=['GET'])
@handle_errors
def get_table_metadata():
    """Get metadata about available tables and their columns"""
    query = """
    SELECT 
        t.TABLE_NAME,
        c.COLUMN_NAME,
        c.DATA_TYPE,
        c.IS_NULLABLE,
        c.CHARACTER_MAXIMUM_LENGTH
    FROM INFORMATION_SCHEMA.TABLES t
    JOIN INFORMATION_SCHEMA.COLUMNS c ON t.TABLE_NAME = c.TABLE_NAME
    WHERE t.TABLE_TYPE = 'BASE TABLE'
        AND t.TABLE_SCHEMA = 'dbo'
        AND t.TABLE_NAME IN ('Users', 'Courses', 'Modules', 'Lessons', 
                             'Enrollments', 'UserProgress', 'Quizzes', 
                             'UserQuizAttempts')
    ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION
    """
    data = query_to_json(query)
    return jsonify(data)

@app.route('/api/metadata/endpoints', methods=['GET'])
def get_endpoints():
    """List all available API endpoints"""
    endpoints = {
        'health': '/api/health',
        'dashboard_summary': '/api/dashboard/summary',
        'courses': '/api/courses',
        'course_detail': '/api/courses/<course_id>',
        'enrollments': '/api/enrollments',
        'enrollment_trends': '/api/enrollments/trends',
        'students': '/api/students',
        'student_progress': '/api/students/<student_id>/progress',
        'detailed_progress': '/api/progress/detailed',
        'category_performance': '/api/categories/performance',
        'quiz_performance': '/api/quiz/performance',
        'engagement_metrics': '/api/engagement/metrics',
        'completion_funnel': '/api/completion/funnel',
        'time_analysis': '/api/time/analysis',
        'filter_enrollments': '/api/enrollments/filter',
        'table_metadata': '/api/metadata/tables',
        'endpoints_list': '/api/metadata/endpoints'
    }
    return jsonify(endpoints)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    # Test database connection on startup
    try:
        conn = get_db_connection()
        print(f"✓ Successfully connected to database: {DATABASE_CONFIG['database']}")
        conn.close()
    except Exception as e:
        print(f"✗ Database connection failed: {e}")
        print("Please check your database configuration and ensure SQL Server is running.")
    
    # Run the API
    print("\n" + "="*60)
    print("LMS REST API Server Starting...")
    print("="*60)
    print(f"Database: {DATABASE_CONFIG['database']}")
    print(f"Server: {DATABASE_CONFIG['server']}")
    print(f"Authentication: Windows Authentication")
    print("API running on: http://localhost:5001")
    print("API documentation: http://localhost:5001/api/metadata/endpoints")
    print("="*60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5001)