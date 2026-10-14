import os
from dotenv import load_dotenv
from google import genai
from groq import Groq

load_dotenv()

g = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
print("Gemini flash models:", [m.name for m in g.models.list() if "flash" in m.name])
r = g.models.generate_content(model=os.environ["GEMINI_MODEL"], contents="Reply with only: OK")
print("Gemini:", r.text.strip())

q = Groq(api_key=os.environ["GROQ_API_KEY"])
print("Groq models:", [m.id for m in q.models.list().data])
r = q.chat.completions.create(
    model=os.environ["GROQ_MODEL"],
    messages=[{"role": "user", "content": "Reply with only: OK"}],
)
print("Groq:", r.choices[0].message.content.strip())