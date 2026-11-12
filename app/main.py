from app.agent.agent import SAPAgent


def main():

    agent = SAPAgent()

    print("=" * 45)
    print("          SAP AI COPILOT")
    print("          Agentic AI - SAP MM")
    print("=" * 45)

    print("\nAsk questions about SAP purchase orders.")
    print("Type 'exit' to quit.\n")

    while True:

        user_input = input("You: ").strip()

        if user_input.lower() == "exit":
            print("\nSAP AI Copilot: Goodbye!")
            break

        if not user_input:
            continue

        try:
            response = agent.ask(user_input)

            print("\nSAP AI Copilot:")
            print(response)
            print()

        except Exception as error:
            print(f"\nError: {error}\n")


if __name__ == "__main__":
    main()