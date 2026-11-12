from app.rag.rag_chain import SAPRAG

def main():

    rag = SAPRAG()

    questions = [
        "Which SAP table stores purchase order item information?",
        "What is the difference between EKKO and EKPO?",
        "What is an ABAP CDS View?",
        "What are the configuration steps for SAP SuccessFactors Employee Central?"
    ]

    for question in questions:

        print("\n" + "=" * 70)
        print(f"QUESTION: {question}")

        answer = rag.ask(question)

        print("\nSAP AI COPILOT:")
        print(answer)


if __name__ == "__main__":
    main()
