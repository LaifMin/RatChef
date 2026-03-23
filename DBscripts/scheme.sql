ALTER TABLE categories
    DROP COLUMN strCategoryThumb,
    DROP COLUMN strCategoryDescription;

ALTER TABLE meals
    DROP COLUMN strArea,
    DROP COLUMN strTags,
    DROP COLUMN strSource,
    DROP COLUMN ingredients;

ALTER TABLE meals
    ADD COLUMN strTime VARCHAR(100),
    ADD COLUMN strDifficulty VARCHAR(100);

ALTER TABLE meals
    DROP COLUMN strCategory;

ALTER TABLE meals
    ADD COLUMN idCategory INT,
    ADD CONSTRAINT fk_meals_category
        FOREIGN KEY (idCategory) REFERENCES categories(idCategory);

ALTER TABLE ingredients
    DROP COLUMN strDescription,
    DROP COLUMN strType;

DROP TABLE IF EXISTS receipeIngredients;

CREATE TABLE IF NOT EXISTS recipeIngredients (
    idIngredient INT NOT NULL,
    idMeal       INT NOT NULL,
    strQta       VARCHAR(50),
    strUnit      VARCHAR(50),
    PRIMARY KEY (idIngredient, idMeal),
    CONSTRAINT fk_ri_ingredient
        FOREIGN KEY (idIngredient) REFERENCES ingredients(idIngredient),
    CONSTRAINT fk_ri_meal
        FOREIGN KEY (idMeal) REFERENCES meals(idMeal)
);

DROP TABLE IF EXISTS prep;

CREATE TABLE IF NOT EXISTS prep (
    idPrep         INT PRIMARY KEY AUTO_INCREMENT,
    strDescription TEXT,
    intProgressive INT,
    idMeal         INT NOT NULL,
    CONSTRAINT fk_prep_meal
        FOREIGN KEY (idMeal) REFERENCES meals(idMeal)
);

DROP TABLE IF EXISTS areas;
