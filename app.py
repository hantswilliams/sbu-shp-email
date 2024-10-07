from flask import Flask, redirect, url_for, session, request, render_template, jsonify
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os
import google.auth.transport.requests
import requests
import traceback
import sqlite3
import urllib.parse
from OpenSSL import SSL
import base64
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

# Factory function to create the Flask app
def create_app():
    app = Flask(__name__)
    app.secret_key = os.urandom(24)  # Keep this secure in production

    # Determine the environment and configure SSL accordingly
    environment = os.getenv('ENVIRONMENT', 'development')
    print(f'Running in {environment} environment')
    if environment == 'production':
        app.config['SSL_CONTEXT'] = None  
    else:
        app.config['SSL_CONTEXT'] = ('cert.pem', 'key.pem')  # Paths to your certificate and private key files for local development

    return app

app = create_app()

# Configuration for Google API
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/userinfo.email',
    'openid'
]
CLIENT_SECRETS_FILE = "client_secret.json"
REDIRECT_URI = "https://127.0.0.1:5000/callback"

# OAuth 2.0 Flow object
flow = Flow.from_client_secrets_file(
    CLIENT_SECRETS_FILE,
    scopes=SCOPES,
    redirect_uri=REDIRECT_URI
)

# Initialize SQLite database
DB_FILE = "credentials.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT,
                    token TEXT,
                    refresh_token TEXT,
                    token_uri TEXT,
                    client_id TEXT,
                    client_secret TEXT,
                    scopes TEXT
                )''')
conn.commit()

@app.route('/')
def index():
    if 'credentials' in session or get_stored_credentials():
        return redirect(url_for('read_messages'))
    return '<a href="/authorize">Authorize Gmail Access</a>'

@app.route('/authorize')
def authorize():
    # Redirect the user to Google's OAuth 2.0 server
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    # Get the user's email address
    service = build('oauth2', 'v2', credentials=credentials)
    user_info = service.userinfo().get().execute()
    email = user_info.get('email')

    credentials_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': ' '.join(credentials.scopes)
    }
    save_credentials(email, credentials_data)
    session['credentials'] = credentials_data
    return redirect(url_for('read_messages'))

@app.route('/read_messages', methods=['GET', 'POST'])
def read_messages():
    if 'credentials' not in session:
        stored_credentials = get_stored_credentials()
        if not stored_credentials:
            return redirect('authorize')
        credentials = google.oauth2.credentials.Credentials(
            **stored_credentials
        )
    else:
        credentials = google.oauth2.credentials.Credentials(
            **session['credentials']
        )

    service = build('gmail', 'v1', credentials=credentials)
    
    # Get search query and type from form
    query = request.form.get('query', '')
    search_type = request.form.get('search_type', 'subject')
    
    # Adjust query based on search type
    if search_type == 'from':
        gmail_query = f"from:{query}"
    else:
        gmail_query = f"subject:{query}"

    # Search for messages
    results = service.users().messages().list(userId='me', q=gmail_query, maxResults=20).execute()
    messages = results.get('messages', [])
    message_details = []

    if messages:
        for msg in messages:
            msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
            ## print the keys of the message data
            print(msg_data.keys())
            message_details.append({
                'id': msg['id'],
                'snippet': msg_data['snippet'],
                'internalDate': msg_data['internalDate'],
                'labelIds': msg_data['labelIds'],
                'sizeEstimate': msg_data['sizeEstimate'],
                'threadId': msg_data['threadId']
            })

    # Save retrieved message details in the session
    session['retrieved_messages'] = message_details 

    return render_template('read_messages.html', query=query, messages=message_details)

@app.route('/view_message/<message_id>', methods=['GET'])
def view_message(message_id):
    if 'credentials' not in session:
        stored_credentials = get_stored_credentials()
        if not stored_credentials:
            return redirect('authorize')
        credentials = google.oauth2.credentials.Credentials(
            **stored_credentials
        )
    else:
        credentials = google.oauth2.credentials.Credentials(
            **session['credentials']
        )

    service = build('gmail', 'v1', credentials=credentials)
    msg_data = service.users().messages().get(userId='me', id=message_id, format='full').execute()
    payload = msg_data.get('payload', {})
    parts = payload.get('parts', [])
    message_content = ''

    # Extract the body from the parts
    for part in parts:
        if part.get('mimeType') == 'text/html':
            body = part.get('body', {}).get('data')
            if body:
                message_content = base64.urlsafe_b64decode(body).decode('utf-8')
                break

    if not message_content:
        message_content = msg_data.get('snippet', 'No content available')

    return jsonify({'message_content': message_content})

@app.route('/execute_code', methods=['POST'])
def execute_code():
    code = request.json.get('code', '')
    messages = session.get('retrieved_messages', [])

    print(f'Executing code: {code}')
    print(f'Messages: {messages}')
    
    # Define a safe environment for exec()
    safe_globals = {
        'messages': messages,
        'output': None,
    }
    
    # Try executing the user code and catching any errors
    try:
        exec(code, safe_globals)
        output = safe_globals.get('output', 'No output defined. Assign a value to `output` in your code.')
    except Exception as e:
        output = f"Error executing code: {traceback.format_exc()}"
    
    return jsonify({'output': output})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

def save_credentials(email, credentials):
    cursor.execute('DELETE FROM credentials WHERE email = ?', (email,))  # Clear existing credentials for the email
    cursor.execute('''INSERT INTO credentials (email, token, refresh_token, token_uri, client_id, client_secret, scopes)
                      VALUES (?, ?, ?, ?, ?, ?, ?)''',
                   (email, credentials['token'], credentials['refresh_token'], credentials['token_uri'],
                    credentials['client_id'], credentials['client_secret'], credentials['scopes']))
    conn.commit()

def get_stored_credentials():
    cursor.execute('SELECT token, refresh_token, token_uri, client_id, client_secret, scopes FROM credentials LIMIT 1')
    row = cursor.fetchone()
    if row:
        return {
            'token': row[0],
            'refresh_token': row[1],
            'token_uri': row[2],
            'client_id': row[3],
            'client_secret': row[4],
            'scopes': row[5].split()
        }
    return None

if __name__ == '__main__':
    app.run('127.0.0.1', 5000, debug=True, ssl_context=app.config['SSL_CONTEXT'])
