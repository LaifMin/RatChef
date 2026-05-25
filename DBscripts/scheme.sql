    


CREATE DATABASE IF NOT EXISTS CUCINA
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE CUCINA;



DROP TABLE IF EXISTS recipeIngredients;
DROP TABLE IF EXISTS prep;
DROP TABLE IF EXISTS meals;
DROP TABLE IF EXISTS ingredients;
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS areas;


CREATE TABLE categories (
    idCategory  INT          NOT NULL AUTO_INCREMENT,
    strCategory VARCHAR(255) NOT NULL,
    PRIMARY KEY (idCategory)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ingredients (
    idIngredient  INT          NOT NULL AUTO_INCREMENT,
    strIngredient VARCHAR(255) NOT NULL,
    PRIMARY KEY (idIngredient)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE areas (
    idArea  INT          NOT NULL AUTO_INCREMENT,
    strArea VARCHAR(255) NOT NULL,
    PRIMARY KEY (idArea)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE meals (
    idMeal          INT          NOT NULL AUTO_INCREMENT,
    strMeal         VARCHAR(255) NOT NULL,
    strInstructions TEXT,
    strTime         VARCHAR(50),
    strDifficulty   VARCHAR(50),
    idCategory      INT,
    PRIMARY KEY (idMeal),
    CONSTRAINT fk_meals_category
        FOREIGN KEY (idCategory) REFERENCES categories(idCategory)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE recipeIngredients (
    idIngredient INT         NOT NULL,
    idMeal       INT         NOT NULL,
    strQta       VARCHAR(50),
    strUnit      VARCHAR(50),
    PRIMARY KEY (idIngredient, idMeal),
    CONSTRAINT fk_ri_ingredient
        FOREIGN KEY (idIngredient) REFERENCES ingredients(idIngredient),
    CONSTRAINT fk_ri_meal
        FOREIGN KEY (idMeal) REFERENCES meals(idMeal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE prep (
    idPrep         INT  NOT NULL AUTO_INCREMENT,
    strDescription TEXT,
    intProgressive INT,
    idMeal         INT  NOT NULL,
    PRIMARY KEY (idPrep),
    CONSTRAINT fk_prep_meal
        FOREIGN KEY (idMeal) REFERENCES meals(idMeal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
