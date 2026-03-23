<?php

header("Content-Type: application/json");
$method = $_SERVER["REQUEST_METHOD"];
$input = file_get_contents("php://input");
$data  = json_decode($input);

if (!$data) {
    echo json_encode(["error" => "Invalid JSON"]);
    exit;
}

$host = "localhost";
$dbname = "CUCINA";
$user = "root";
$pass = "";

if($method == "POST"){

    try{

    $conn = new PDO("mysql:host=$host;dbname=$dbname",$user,$pass);
    $query = "INSERT INTO meals
            (idMeal, strMeal, strCategory, strArea, strInstructions, strTags, strSource, ingredients)
        VALUES
            (:idMeal, :strMeal, :strCategory, :strArea, :strInstructions, :strTags, :strSource, :ingredients)
        ON DUPLICATE KEY UPDATE
            strMeal         = VALUES(strMeal),
            strInstructions = VALUES(strInstructions),
            ingredients     = VALUES(ingredients)
    ";
    
    $params = [
        ':idMeal'          => (int) $data->idMeal,
        ':strMeal'         => $data->strMeal         ?? null,
        ':strCategory'     => $data->strCategory     ?? null,
        ':strArea'         => $data->strArea         ?? null,
        ':strInstructions' => $data->strInstructions ?? null,
        ':strTags'         => $data->strTags         ?? null,
        ':strSource'       => $data->strSource       ?? null,
        ':ingredients'     => $data->ingredients     ?? null,
    ];
    $stm = $conn->prepare($query);
    $stm->execute($params);
    echo json_encode(["success" => true]);

    

    }catch(Exception $e) {
    echo json_encode(["error" => $e->getMessage()]);
}

}




?> 