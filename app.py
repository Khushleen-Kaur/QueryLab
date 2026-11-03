import os
from flask import Flask, jsonify, render_template, request
import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# Configure Aiven Cloud MySQL connection pool
db_config = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "ssl_disabled": os.getenv("DB_SSL_DISABLED", "false").lower() == "true",
}

# Optional: Add SSL CA certificate if required by Aiven
ssl_ca = os.getenv("DB_SSL_CA")
if ssl_ca and os.path.exists(ssl_ca):
    db_config["ssl_ca"] = ssl_ca

# Initialize connection pool to handle concurrent team requests efficiently
try:
  db_pool = pooling.MySQLConnectionPool(
      pool_name="querylab_pool", pool_size=5, **db_config
  )
  print(" Successfully connected to Aiven Cloud MySQL pool!")
except Exception as e:
  print(f" Database connection pool initialization failed: {e}")
  db_pool = None


@app.route("/")
def index():
  """Renders the main QueryLab frontend application interface."""
  return render_template("index.html")


@app.route("/api/tables", methods=["GET"])
def get_tables():
  """Retrieves all active table names and row counts from the Aiven database

  for the sidebar Database Explorer.
  """
  if not db_pool:
    return jsonify({"success": False, "error": "Database pool not initialized."}), 500

  connection = None
  cursor = None
  try:
    connection = db_pool.get_connection()
    cursor = connection.cursor(dictionary=True)

    # Fetch all tables in the current database
    cursor.execute("SHOW TABLES;")
    tables_result = cursor.fetchall()

    table_list = []
    for row in tables_result:
      table_name = list(row.values())[0]

      # Get row count and column information for each table
      cursor.execute(f"SELECT COUNT(*) as count FROM `{table_name}`;")
      count_res = cursor.fetchone()
      row_count = count_res["count"] if count_res else 0

      cursor.execute(f"DESCRIBE `{table_name}`;")
      columns = cursor.fetchall()

      table_list.append({
          "name": table_name,
          "rows": row_count,
          "columns": [col["Field"] for col in columns],
      })

    return jsonify({"success": True, "tables": table_list})

  except Exception as e:
    return jsonify({"success": False, "error": str(e)}), 500
  finally:
    if cursor:
      cursor.close()
    if connection:
      connection.close()


@app.route("/api/execute", methods=["POST"])
def execute_query():
  """Receives SQL queries from the frontend, executes them on Aiven Cloud MySQL,

  and returns formatted result sets or execution status.
  """
  if not db_pool:
    return jsonify({"success": False, "error": "Database connection unavailable."}), 500

  data = request.get_json()
  if not data or "query" not in data:
    return jsonify({"success": False, "error": "No SQL query provided in request."}), 400

  raw_query = data["query"].strip()

  # Basic security safeguard against dropping entire databases
  if "DROP DATABASE" in raw_query.upper():
    return jsonify({"success": False, "error": "DROP DATABASE is restricted in QueryLab sandbox."}), 403

  connection = None
  cursor = None
  try:
    connection = db_pool.get_connection()
    # Use buffered cursor to handle multi-result or statement sets properly
    cursor = connection.cursor(buffered=True, dictionary=True)

    # Execute user SQL statement
    cursor.execute(raw_query)

    # Check if query returns rows (e.g. SELECT, SHOW, DESCRIBE)
    if cursor.with_rows:
      rows = cursor.fetchall()
      columns = list(rows[0].keys()) if rows else []
      # Convert rows to standard list of lists / dicts for JSON serialization
      result_data = [[row[col] for col in columns] for row in rows]

      connection.commit()
      return jsonify({
          "success": True,
          "type": "select",
          "columns": columns,
          "data": result_data,
          "row_count": len(result_data),
          "message": f"Query executed successfully. Returned {len(result_data)} rows.",
      })
    else:
      # For DDL/DML statements (CREATE, INSERT, UPDATE, DELETE)
      connection.commit()
      affected_rows = cursor.rowcount
      return jsonify({
          "success": True,
          "type": "action",
          "affected_rows": affected_rows,
          "message": f"Query OK, {max(affected_rows, 0)} row(s) affected.",
      })

  except mysql.connector.Error as err:
    if connection:
      connection.rollback()
    return jsonify({
        "success": False,
        "error": f"MySQL Error [{err.errno}]: {err.msg}",
    }), 400
  except Exception as e:
    if connection:
      connection.rollback()
    return jsonify({"success": False, "error": str(e)}), 400
  finally:
    if cursor:
      cursor.close()
    if connection:
      connection.close()


if __name__ == "__main__":
  app.run(debug=True, port=5000)