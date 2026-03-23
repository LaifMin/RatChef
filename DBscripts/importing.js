
const alphabet = 'abcdefghijklmnopqrstuvwxyz'.split('');

console.log("hello world")
async function getCatalog(){
    const receipt = await fetch ("https://www.themealdb.com/api/json/v1/1/categories.php", {

    method: "GET",
    headers: {"Content-Type": "application/json"},
    });

    const data = await receipt.json();
    
    data.categories.forEach(element => {
        console.log(element)
    });
    return data.categories;
}

async function getCatalogIntoDB(){
    const data = await getCatalog()
    for (const category of data) {   
        const response = await fetch("http://localhost/RatChef/AiAgentJunior/DBscripts/save.php", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(category)  
        });

        const result = await response.json();
        console.log(result);
    }

}

async function getMeals(letter){

    const receipt = await fetch (`https://www.themealdb.com/api/json/v1/1/search.php?f=${letter}`, {

    method: "GET",
    headers: {"Content-Type": "application/json"},
    });

    const data = await receipt.json();

    if (!data.meals) {
        console.log(`No meals for '${letter}', skipping.`);
        return [];
    }

    data.meals.forEach(element => {
        console.log(element)
    });
    return data.meals;
}

async function saveMealIntoDB(meal) {
    const payload = {
        idMeal:          meal.idMeal,
        strMeal:         meal.strMeal,
        strCategory:     meal.strCategory,
        strArea:         meal.strArea,
        strInstructions: meal.strInstructions,
        strTags:         meal.strTags,
        strSource:       meal.strSource,
        ingredients:     JSON.stringify(extractIngredients(meal))
    };

    const response = await fetch("http://localhost/RatChef/AiAgentJunior/DBscripts/saveMeals.php", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
    });

    const result = await response.json();
    return result;
}

function extractIngredients(meal) {
    const ingredients = [];
    for (let i = 1; i <= 20; i++) {
        const ingredient = (meal[`strIngredient${i}`] || "").trim();
        const measure    = (meal[`strMeasure${i}`]    || "").trim();
        if (ingredient) {
            ingredients.push({ ingredient, measure });
        }
    }
    return ingredients;
}


async function importAllMeals() {
    let totalImported = 0;
    let totalSkipped  = 0;

    for (const letter of alphabet) {
        console.log(`\n—Letter: ${letter.toUpperCase()}`);
        const meals = await getMeals(letter);

        for (const meal of meals) {
            const result = await saveMealIntoDB(meal);
            if (result.success) {
                console.log(`—Saved: ${meal.strMeal}`);
                totalImported++;
            } else {
                console.log(`—Failed: ${meal.strMeal} — ${result.error}`);
                totalSkipped++;
            }
        }
    }

    console.log(`\nDone! Imported: ${totalImported} | Skipped: ${totalSkipped}`);
}

importAllMeals();

async function importIngredients() {
    const receipt = await fetch("https://www.themealdb.com/api/json/v1/1/list.php?i=list", {
        method: "GET",
        headers: {"Content-Type": "application/json"},
    });

    const data = await receipt.json();

    if (!data.meals) {
        console.log("No ingredients found.");
        return;
    }

    for (const ingredient of data.meals) {
        const response = await fetch("http://localhost/RatChef/AiAgentJunior/DBscripts/saveIngredients.php", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(ingredient)
        });
        const result = await response.json();
        console.log(result.success ? `Saved ingredient: ${ingredient.strIngredient}` : `Failed: ${result.error}`);
    }
}

async function importAreas() {
    const receipt = await fetch("https://www.themealdb.com/api/json/v1/1/list.php?a=list", {
        method: "GET",
        headers: {"Content-Type": "application/json"},
    });

    const data = await receipt.json();

    if (!data.meals) {
        console.log("No areas found.");
        return;
    }

    for (const area of data.meals) {
        const response = await fetch("http://localhost/RatChef/AiAgentJunior/DBscripts/saveAreas.php", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(area)
        });
        const result = await response.json();
        console.log(result.success ? `Saved area: ${area.strArea}` : `Failed: ${result.error}`);
    }
}

importIngredients();
importAreas();