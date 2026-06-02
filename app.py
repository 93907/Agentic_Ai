from flask import Flask, render_template, request, redirect, session, send_file
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from groq import Groq

import pyotp
import qrcode
import os
import fitz
import uuid
from datetime import datetime
from numpy import dot
from numpy.linalg import norm
import json
import re

app = Flask(__name__)
app.secret_key = "secretkey"

# ---------------------------------

# GROQ

# ---------------------------------

groq_client = Groq(
api_key="your_groq_api_key"
)

# ---------------------------------

# MONGODB

# ---------------------------------

client = MongoClient("mongodb://localhost:27017/")

db = client["chatbot_db"]

users = db["users"]

chat_history_db = db["chat_history"]

documents_db = db["documents"]

document_chunks_db = db["document_chunks"]

search_history_db = db["search_history"]

# ---------------------------------

# EMBEDDING MODEL

# ---------------------------------

embedding_model = SentenceTransformer(
"all-MiniLM-L6-v2"
)

# ---------------------------------

# FOLDERS

# ---------------------------------

os.makedirs("qr_codes", exist_ok=True)
os.makedirs("uploads", exist_ok=True)

# ---------------------------------

# HELPER FUNCTIONS
# ---------------------------------

# HELPER FUNCTIONS

# ---------------------------------

def split_text(text):

    chunk_size = 1200
    overlap = 200

    chunks = []

    for i in range(0, len(text), chunk_size - overlap):

        chunks.append(
            text[i:i + chunk_size]
        )

    return chunks


def cosine_similarity(a, b):


    return dot(a, b) / (
    norm(a) * norm(b)
)

def get_relevant_context(question):

    query_embedding = embedding_model.encode(
        question
    ).tolist()

    all_chunks = list(
        document_chunks_db.find()
    )

    results = []

    for item in all_chunks:

        try:

            score = cosine_similarity(
                query_embedding,
                item["embedding"]
            )

            results.append({

                "score": score,

                "document": item.get(
                    "document",
                    "Unknown"
                ),

                "chunk": item["chunk"]

            })

        except Exception:
            continue

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # Get top 10 chunks instead of 3
    top_chunks = results[:10]

    context = ""

    for item in top_chunks:

        context += f"""

Document:
{item['document']}

Content:
{item['chunk']}

----------------------------------

"""

    return context



# ---------------------------------

# HOME

# ---------------------------------

@app.route('/')
def home():
 return redirect('/register')

# ---------------------------------

# REGISTER

# ---------------------------------

@app.route('/register', methods=['GET', 'POST'])
def register():

    if request.method == 'POST':

        username = request.form['username']
        password = request.form['password']
        role = request.form['role']

        if users.find_one({"username": username}):
            return "Username already exists"

        hashed_password = generate_password_hash(password)

        secret = pyotp.random_base32()

        users.insert_one({
            "username": username,
            "password": hashed_password,
            "secret": secret,
            "role": role
        })

        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=username,
            issuer_name="AcademicAssistant"
        )

        img = qrcode.make(uri)
        img.save(f"qr_codes/{username}.png")

        return f"""
        Registration Successful

        <br><br>

        Scan QR Code

        <br><br>

        <img src='/qr/{username}'>

        <br><br>

        <a href='/login'>Login</a>
        """

    return render_template("register.html")


@app.route('/qr/<username>')
def qr(username):


 return send_file(
    f"qr_codes/{username}.png"
)


# ---------------------------------
# LOGIN
# ---------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        username = request.form['username']
        password = request.form['password']

        user = users.find_one({
            "username": username
        })

        if user and check_password_hash(
            user["password"],
            password
        ):

            session["username"] = username

            # Start fresh chat for this user
            session["chat_history"] = []

            return redirect('/otp')

        else:
            return "Invalid Credentials"

    return render_template("login.html")


# ---------------------------------
# OTP
# ---------------------------------

@app.route('/otp', methods=['GET', 'POST'])
def otp():

    if "username" not in session:
        return redirect('/login')

    if request.method == 'POST':

        otp_code = request.form['otp']

        user = users.find_one({
            "username": session["username"]
        })

        if not user:
            return "User not found"

        totp = pyotp.TOTP(user["secret"])

        if totp.verify(otp_code):
            return redirect('/dashboard')
        else:
            return "Invalid OTP"

    return render_template("otp.html")

# ---------------------------------

# DASHBOARD

# ---------------------------------

@app.route('/dashboard')
def dashboard():
    if "username" not in session:
        return redirect('/login')

    return render_template(
        "dashboard.html"
    )

# ---------------------------------

# PDF UPLOAD + EMBEDDINGS

# ---------------------------------

@app.route('/upload_pdf', methods=['GET', 'POST'])
def upload_pdf():
    if "username" not in session:
        return redirect('/login')

    user = users.find_one({
        "username": session["username"]
    })

    if user["role"] != "faculty":
        return """
    <script>
    alert('This section is not available for students.');
    window.location.href='/dashboard';
    </script>
    """
    if request.method == "POST":

        pdf = request.files["pdf"]

        if pdf:

            filename = pdf.filename

            filepath = os.path.join(
                "uploads",
                filename
            )

            pdf.save(filepath)

            doc = fitz.open(filepath)

            text = ""

            for page in doc:

                text += page.get_text()

            documents_db.insert_one({

                "filename": filename,
                "uploaded_by": session["username"],
                "upload_date": datetime.now(),
                "text": text

            })

            chunks = split_text(text)

            for chunk in chunks:

                embedding = embedding_model.encode(
                    chunk
                ).tolist()

                document_chunks_db.insert_one({

                    "document": filename,
                    "chunk": chunk,
                    "embedding": embedding

                })

            return f"""
<script>
alert('PDF Uploaded Successfully!');
window.location.href='/dashboard';
</script>
"""

    return render_template(
        "upload_pdf.html"
    )


# ---------------------------------

# ---------------------------------
# RAG CHATBOT
# ---------------------------------

@app.route('/chatbot', methods=['GET', 'POST'])
def chatbot():

    if "username" not in session:
        return redirect('/login')

    if "chat_history" not in session:
        session["chat_history"] = []

    if request.method == "POST":

        question = request.form["message"]

        context = get_relevant_context(question)

        previous_chat = ""

        for msg in session["chat_history"][-6:]:
            previous_chat += f"{msg['role']}: {msg['content']}\n"

        prompt = f"""
You are an intelligent Academic AI Assistant.

You help students learn from uploaded PDFs.

Previous Conversation:
{previous_chat}

Relevant PDF Content:
{context}

Student Question:
{question}

Rules:

1. Understand the student's intent.

2. If the student asks:
   - topics
   - syllabus
   - modules
   - units
   - index
   - contents

   Return ONLY a clean numbered list.

3. If the student asks:
   - what is
   - explain
   - describe

   Return:

   Definition
   Explanation
   Importance
   Example

4. If the student asks:
   - advantages

   Return only advantages.

5. If the student asks:
   - disadvantages

   Return only disadvantages.

6. If the student asks:
   - compare
   - difference between

   Return a comparison table.

7. If the student asks:
   - types

   Return all types with explanations.

8. If the student asks:
   - summary

   Return a concise summary.

9. If the student asks:
   - quiz

   Generate MCQs with answers.

10. Use PDF information first.

11. If PDF contains partial information,
    expand using academic knowledge.

12. Use headings and bullet points.

13. Avoid huge paragraphs.

14. Give exam-oriented answers.

15. Answer according to the question,
    not the same format every time.

Answer:
"""

        try:

            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,
                max_tokens=1500
            )

            answer = completion.choices[0].message.content

            session["chat_history"].append({
                "role": "user",
                "content": question
            })

            session["chat_history"].append({
                "role": "assistant",
                "content": answer
            })

            session.modified = True

            chat_history_db.insert_one({
                "username": session["username"],
                "question": question,
                "answer": answer,
                "timestamp": datetime.now()
            })

            search_history_db.insert_one({
                "username": session["username"],
                "question": question,
                "answer": answer,
                "timestamp": datetime.now()
            })

        except Exception as e:

            session["chat_history"].append({
                "role": "assistant",
                "content": f"Error: {str(e)}"
            })

    return render_template(
        "chatbot.html",
        chat_history=session["chat_history"]
    )

# ---------------------------------

# QUIZ GENERATOR

# ---------------------------------

@app.route('/generate_quiz', methods=['POST'])
def generate_quiz():

    topic = request.form["topic"]

    context = get_relevant_context(
        topic
    )

    prompt = f"""
Using the study material below:

{context}

Generate:

1. 10 MCQ Questions
2. 4 Options each
3. Correct Answer
4. Short Explanation

Format:

Q1:
Options:
A)
B)
C)
D)

Answer:

Explanation:

Repeat for all 10 questions.
"""

    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return completion.choices[0].message.content

@app.route('/generate_notes', methods=['POST'])
def generate_notes():

    topic = request.form["topic"]

    context = get_relevant_context(
        topic
    )

    prompt = f"""
Create complete study notes.

Context:

{context}

Include:

1. Definition
2. Introduction
3. Importance
4. Key Concepts
5. Advantages
6. Disadvantages
7. Applications
8. Comparison Table
9. Real World Examples
10. Interview Questions
11. Exam Questions
12. Summary
"""

    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return completion.choices[0].message.content


# ---------------------------------

# NOTES SUMMARIZER

# ---------------------------------

@app.route('/summarize', methods=['POST'])
def summarize():

    topic = request.form["topic"]

    context = get_relevant_context(topic)

    prompt = f"""
Summarize the following notes.

{context}
"""

    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return completion.choices[0].message.content


# ---------------------------------

# HISTORY

# ---------------------------------

@app.route('/history')
def history():

    if "username" not in session:
        return redirect('/login')

    history_data = list(
        search_history_db.find(
            {"username": session["username"]}
        ).sort("timestamp", -1)
    )

    return render_template(
        "history.html",
        history=history_data
    )
#--------------------------------
# GENERATE QUIZ
#---------------------------------  

# --------------------------------
# GENERATE QUIZ
# --------------------------------

@app.route('/quiz_setup')
def quiz_setup():

    if "username" not in session:
        return redirect('/login')

    return render_template("quiz_setup.html")


@app.route('/start_quiz', methods=['POST'])
def start_quiz():

    topic = request.form['topic']
    num_questions = request.form['num_questions']
    duration = request.form['duration']

    context = get_relevant_context(topic)

    prompt = f"""
Generate EXACTLY {num_questions} MCQ questions.

Topic:
{topic}

Use ONLY the content below.

{context}

Return ONLY valid JSON.

Example:

[
    {{
        "question":"What is Malware?",
        "options":[
            "Virus",
            "Router",
            "Switch",
            "Printer"
        ],
        "answer":"Virus"
    }}
]

Rules:

1. Return JSON only.
2. No markdown.
3. No explanations.
4. No extra text.
5. Every question must have 4 options.
6. Questions must be from the PDF content.
"""

    try:

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            max_tokens=3000
        )

        quiz_text = completion.choices[0].message.content

        quiz_json = json.loads(quiz_text)

        session["quiz"] = quiz_json
        session["duration"] = int(duration)
        session["topic"] = topic

        return redirect('/quiz')

    except Exception as e:

        return f"Quiz Generation Error: {str(e)}"


@app.route('/quiz')
def quiz():

    if "quiz" not in session:
        return redirect('/quiz_setup')

    return render_template(
        "quiz.html",
        questions=session["quiz"],
        duration=session["duration"],
        topic=session["topic"]
    )


@app.route('/submit_quiz', methods=['POST'])
def submit_quiz():

    questions = session["quiz"]

    score = 0

    results = []

    for i, q in enumerate(questions):

        user_answer = request.form.get(f"q{i}")

        correct_answer = q["answer"]

        if user_answer == correct_answer:
            score += 1

        results.append({
            "question": q["question"],
            "user_answer": user_answer,
            "correct_answer": correct_answer
        })

    total = len(questions)

    percentage = round((score / total) * 100, 2)

    return render_template(
        "quiz_result.html",
        score=score,
        total=total,
        percentage=percentage,
        results=results
    )

# ---------------------------------

# LOGOUT

# ---------------------------------

@app.route('/logout')
def logout():

    session.clear()

    return redirect('/login')






# ---------------------------------

# RUN APP

# ---------------------------------

if __name__ == "__main__":


    app.run(
    debug=True,
    port=5001
)
