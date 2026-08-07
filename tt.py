from flask import Flask, render_template_string

app = Flask(__name__)

@app.route('/')
def index():
    return render_template_string("""
    asdasdasd
    """)

app.run(port=8800,host='0.0.0.0',ssl_context='adhoc')