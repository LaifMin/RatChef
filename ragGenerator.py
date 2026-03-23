import yaml
import pymysql
from fpdf import FPDF
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import InMemoryVectorStore

# Carico configurazione
with open("config.yml", 'r') as f:
    config = yaml.safe_load(f)

db_conf = config['db']
pdf_path = config.get('pdf_path', './vs/recipes.pdf')
rag_db_path = config.get('rag_db_path', './vs/recipes.db')
embed_model = config['models']['embed']


def fetch_recipes():
    """Legge tutte le ricette dal DB MySQL CUCINA con ingredienti e step di preparazione."""
    conn = pymysql.connect(
        host=db_conf['host'],
        user=db_conf['user'],
        password=db_conf['pass'],
        database=db_conf['name'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

    recipes = []

    try:
        with conn.cursor() as cursor:
            # Prendo tutti i meals con la categoria
            cursor.execute("""
                SELECT m.idMeal, m.strMeal, m.strInstructions, m.strTime, m.strDifficulty,
                       c.strCategory
                FROM meals m
                LEFT JOIN categories c ON m.idCategory = c.idCategory
                ORDER BY m.strMeal
            """)
            meals = cursor.fetchall()

            for meal in meals:
                meal_id = meal['idMeal']

                # Ingredienti per questa ricetta
                cursor.execute("""
                    SELECT i.strIngredient, ri.strQta, ri.strUnit
                    FROM recipeIngredients ri
                    JOIN ingredients i ON ri.idIngredient = i.idIngredient
                    WHERE ri.idMeal = %s
                """, (meal_id,))
                ingredients = cursor.fetchall()

                # Step di preparazione
                cursor.execute("""
                    SELECT strDescription, intProgressive
                    FROM prep
                    WHERE idMeal = %s
                    ORDER BY intProgressive
                """, (meal_id,))
                steps = cursor.fetchall()

                recipes.append({
                    'name': meal.get('strMeal', 'Senza nome'),
                    'category': meal.get('strCategory', ''),
                    'instructions': meal.get('strInstructions', ''),
                    'time': meal.get('strTime', ''),
                    'difficulty': meal.get('strDifficulty', ''),
                    'ingredients': ingredients,
                    'steps': steps
                })

    finally:
        conn.close()

    return recipes


def sanitize_text(text):
    """Rimuove caratteri non supportati da latin-1 per fpdf2."""
    if not text:
        return ""
    return text.encode('latin-1', errors='replace').decode('latin-1')


def generate_pdf(recipes, output_path):
    """Genera un PDF con tutte le ricette."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Pagina di copertina
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 28)
    pdf.cell(0, 60, "", ln=True)
    pdf.cell(0, 20, sanitize_text("RatChef - Libro delle Ricette"), ln=True, align="C")
    pdf.set_font("Helvetica", "", 14)
    pdf.cell(0, 15, sanitize_text(f"Totale ricette: {len(recipes)}"), ln=True, align="C")

    for recipe in recipes:
        pdf.add_page()

        # Titolo ricetta
        pdf.set_font("Helvetica", "B", 20)
        pdf.cell(0, 12, sanitize_text(recipe['name']), ln=True)
        pdf.ln(3)

        # Categoria, tempo, difficolta
        pdf.set_font("Helvetica", "", 11)
        meta_parts = []
        if recipe['category']:
            meta_parts.append(f"Categoria: {recipe['category']}")
        if recipe['time']:
            meta_parts.append(f"Tempo: {recipe['time']}")
        if recipe['difficulty']:
            meta_parts.append(f"Difficolta: {recipe['difficulty']}")
        if meta_parts:
            pdf.cell(0, 8, sanitize_text(" | ".join(meta_parts)), ln=True)
            pdf.ln(3)

        # Ingredienti
        if recipe['ingredients']:
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 10, "Ingredienti", ln=True)
            pdf.set_font("Helvetica", "", 11)
            for ing in recipe['ingredients']:
                name = ing.get('strIngredient', '')
                qty = ing.get('strQta', '')
                unit = ing.get('strUnit', '')
                line = f"  - {name}"
                if qty:
                    line += f": {qty}"
                if unit:
                    line += f" {unit}"
                pdf.cell(0, 7, sanitize_text(line), ln=True)
            pdf.ln(3)

        # Step di preparazione
        if recipe['steps']:
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 10, "Preparazione", ln=True)
            pdf.set_font("Helvetica", "", 11)
            for step in recipe['steps']:
                num = step.get('intProgressive', '')
                desc = step.get('strDescription', '')
                step_text = f"  {num}. {desc}" if num else f"  - {desc}"
                pdf.multi_cell(0, 7, sanitize_text(step_text))
            pdf.ln(3)

        # Istruzioni generali (dal campo strInstructions)
        if recipe['instructions']:
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 10, "Istruzioni", ln=True)
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(0, 7, sanitize_text(recipe['instructions']))

    pdf.output(output_path)
    print(f"PDF generato: {output_path} ({len(recipes)} ricette)")


def build_vector_store(pdf_path, rag_db_path, embed_model):
    """Carica il PDF nel RAG vector store."""
    # Carico il PDF
    loader = PyPDFLoader(pdf_path)
    docs = loader.load()
    print(f"Documenti caricati dal PDF: {len(docs)}")

    # Split in parti piu piccole
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=500,
        length_function=len,
        is_separator_regex=False
    )
    chunks = text_splitter.split_documents(docs)
    print(f"Chunks creati: {len(chunks)}")

    # Creo il vector store
    embeddings = OllamaEmbeddings(model=embed_model)
    vs = InMemoryVectorStore.from_documents(chunks, embeddings)
    print("Vector store creato")

    # Salvo
    vs.dump(rag_db_path)
    print(f"Vector store salvato: {rag_db_path}")


if __name__ == "__main__":
    print("=== RatChef RAG Generator ===")
    print("1. Lettura ricette dal database...")
    recipes = fetch_recipes()
    print(f"   Trovate {len(recipes)} ricette")

    print("2. Generazione PDF...")
    generate_pdf(recipes, pdf_path)

    print("3. Creazione vector store RAG...")
    build_vector_store(pdf_path, rag_db_path, embed_model)

    print("\n=== Completato! ===")