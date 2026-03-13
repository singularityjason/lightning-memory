"""
Example: Using Lightning Memory with LangChain via MCP.

This example demonstrates how to connect a LangChain agent to the 
Lightning Memory MCP server.
"""

import asyncio
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
# Note: You'll need langchain-mcp or a similar bridge to use MCP tools directly
# For this example, we show the conceptual integration.

async def main():
    # 1. Initialize the LLM
    llm = ChatOpenAI(model="gpt-4o", temperature=0)

    # 2. Define tools (In practice, these come from the MCP server)
    # Example tool definitions matching Lightning Memory's capabilities
    tools = [
        {
            "name": "memory_add",
            "description": "Store a new memory string in the long-term database",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The content to remember"}
                },
                "required": ["text"]
            }
        },
        {
            "name": "memory_search",
            "description": "Search for relevant memories by semantic similarity",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term"}
                },
                "required": ["query"]
            }
        }
    ]

    # 3. Define the prompt
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an AI with persistent memory. Use 'memory_add' to save important info and 'memory_search' to recall it."),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    # 4. Create the agent
    # In a real MCP setup, you'd use a tool provider that connects to the 
    # Lightning Memory MCP server (e.g., via `npx lightning-memory`)
    print("Agent initialized with Lightning Memory tools...")
    
    # conceptual usage
    print("User: My favorite programming language is Python. Remember that.")
    print("Agent: [Calling memory_add(text='User favorite language is Python')] I'll remember that!")

if __name__ == "__main__":
    asyncio.run(main())
