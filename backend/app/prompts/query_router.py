QUERY_ROUTER_PROMPT = """Classify the user's query into one of three categories:

1. "kb" - The query asks about specific information that would be found in a financial document (annual report, MD&A, etc.). Examples:
   - "What was the net income for 2025?"
   - "How did the credit loss provisions change?"
   - "Summarize the risk factors section"
   - "What are the key performance metrics?"

2. "general" - The query asks a general finance/business question not tied to any specific document. Examples:
   - "What is net income?"
   - "How does credit risk work?"
   - "Explain IFRS 9"
   - "What is a provision for credit losses?"

3. "needs_clarification" - The query is ambiguous and could refer to multiple distinct sections of a financial document. The user should pick a specific section. Examples:
   - "Tell me about banking" (could be personal banking, commercial banking, capital markets)
   - "What about risk?" (credit risk, market risk, operational risk, etc.)
   - "Revenue breakdown" (by segment, by geography, by product)

Respond with ONLY one of: kb, general, needs_clarification

User query: {query}
Classification:"""
