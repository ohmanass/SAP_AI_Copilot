from dotenv import load_dotenv

load_dotenv()

from gen_ai_hub.proxy.native.openai import chat


def test_llm_connection():

    response = chat.completions.create(
        model_name="gpt-4o-mini",               #Modèle LLM utilisé 
        messages=[                              # Hist
            {
                "role": "system",
                "content": "You are an expert SAP technical consultant."
            },
            {
                "role": "user",
                "content": "Explain me how to create a purchase order, then, create a pdf file which explain all SAP modules"
            }
        ],
        temperature=0,
        max_tokens=300
    )

    print(response.choices[0].message.content)


if __name__ == "__main__":
    test_llm_connection()