import logging
import traceback
import pymysql


def get_db_connection(db_conf):
    return pymysql.connect(
        host=db_conf['host'],
        user=db_conf['user'],
        password=db_conf['pass'],
        database=db_conf['name'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )


def get_existing_meal_names(db_conf):
    try:
        conn = get_db_connection(db_conf)
        with conn.cursor() as cursor:
            cursor.execute("SELECT strMeal FROM meals")
            rows = cursor.fetchall()
        conn.close()
        return [r['strMeal'] for r in rows]
    except Exception as e:
        logging.error(f"Errore lettura ricette esistenti: {e}")
        return []


def save_recipe_to_db(recipes, db_conf):
    if isinstance(recipes, dict):
        recipes = [recipes]

    conn = None
    results = []

    try:
        conn = get_db_connection(db_conf)
    except Exception as e:
        logging.error(f"Errore connessione DB: {e}")
        return [{'meal': 'all', 'status': 'error', 'reason': str(e)}]

    for recipe_json in recipes:
        meal_name = ''
        id_meal = None
        try:
            meal_data        = recipe_json.get('meal', {})
            ingredients_list = recipe_json.get('ingredients', [])
            prep_list        = recipe_json.get('prep', [])
            meal_name        = meal_data.get('strMeal', '').strip()

            if not meal_name:
                continue

            with conn.cursor() as cursor:
                cursor.execute("SELECT idMeal FROM meals WHERE strMeal = %s", (meal_name,))
                if cursor.fetchone():
                    results.append({'meal': meal_name, 'status': 'skipped', 'reason': 'già esistente'})
                    continue

                category_name = meal_data.get('idCategory', 'Generica')
                cursor.execute("SELECT idCategory FROM categories WHERE strCategory = %s", (category_name,))
                cat_result = cursor.fetchone()
                if cat_result:
                    id_category = cat_result['idCategory']
                else:
                    cursor.execute("INSERT INTO categories (strCategory) VALUES (%s)", (category_name,))
                    id_category = cursor.lastrowid

                cursor.execute(
                    "INSERT INTO meals (strMeal, strInstructions, idCategory) VALUES (%s, %s, %s)",
                    (meal_name, meal_data.get('strInstructions', ''), id_category)
                )
                id_meal = cursor.lastrowid

                for ing in ingredients_list:
                    ing_name = ing.get('strIngredient', '').strip()
                    if not ing_name:
                        continue
                    cursor.execute("SELECT idIngredient FROM ingredients WHERE strIngredient = %s", (ing_name,))
                    ing_result = cursor.fetchone()
                    if ing_result:
                        id_ingredient = ing_result['idIngredient']
                    else:
                        cursor.execute("INSERT INTO ingredients (strIngredient) VALUES (%s)", (ing_name,))
                        id_ingredient = cursor.lastrowid
                    cursor.execute(
                        "INSERT IGNORE INTO recipeIngredients (idIngredient, idMeal, strQta, strUnit) VALUES (%s, %s, %s, %s)",
                        (id_ingredient, id_meal, ing.get('strQta', ''), ing.get('strUnit', ''))
                    )

                for i, step in enumerate(prep_list):
                    cursor.execute(
                        "INSERT INTO prep (strDescription, intProgressive, idMeal) VALUES (%s, %s, %s)",
                        (step.get('strDescription', ''), step.get('intProgressive', i + 1), id_meal)
                    )

            conn.commit()
            results.append({'meal': meal_name, 'status': 'saved'})

        except Exception as e:
            conn.rollback()
            logging.error(f"Errore salvataggio '{meal_name}': {e}")
            traceback.print_exc()
            results.append({'meal': meal_name, 'status': 'error', 'reason': str(e)})

    if conn:
        conn.close()
    return results


def format_save_results(results):
    saved   = [r['meal'] for r in results if r['status'] == 'saved']
    skipped = [r['meal'] for r in results if r['status'] == 'skipped']
    errors  = [r         for r in results if r['status'] == 'error']
    parts = []
    if saved:   parts.append(f"Salvate: {', '.join(saved)}")
    if skipped: parts.append(f"Già presenti (saltate): {', '.join(skipped)}")
    if errors:  parts.append(f"Errori: {', '.join(r['meal'] for r in errors)}")
    return ' | '.join(parts) if parts else 'Nessuna ricetta processata.'
