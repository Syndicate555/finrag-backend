RAG_PROMPT = """Answer the user's question using ONLY the provided context from the financial document. Follow these rules strictly:

1. Cite sources using [p.XX] format where XX is the page number
2. If multiple chunks support a point, cite all relevant pages
3. If the context doesn't contain enough information to answer, say "Based on the available document sections, I don't have enough information to fully answer this question."
4. Reproduce key figures and tables when relevant
5. Be precise with financial numbers — do not round unless the source does
6. Distinguish between direct quotes and your synthesis

Context from document:
{context}

User question: {query}

Answer:"""

RAG_PROMPT_WITH_SECTION = """Answer the user's question using ONLY the provided context from the financial document. Focus specifically on the "{section}" section.

Follow these rules strictly:
1. Cite sources using [p.XX] format where XX is the page number
2. If multiple chunks support a point, cite all relevant pages
3. If the context doesn't contain enough information to answer, say so clearly
4. Reproduce key figures and tables when relevant
5. Be precise with financial numbers

Context from document:
{context}

User question: {query}

Answer:"""
