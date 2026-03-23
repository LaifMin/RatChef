<?php

header("Content-Type: application/json");
$method = $_SERVER["REQUEST_METHOD"];
$input = file_get_contents("php://input");


$host = "localhost";
$dbname = "CUCINA";
$user = "root";
$pass = "";

if($method == "POST"){

    try{

    $conn = new PDO("mysql:host=$host;dbname=$dbname",$user,$pass);
    $query = "INSERT INTO CATEGORIES (`idCategory`, `strCategory`, `strCategoryThumb`, `strCategoryDescription`) 
          VALUES (:idCategory, :strCategory, :strCategoryThumb, :strCategoryDescription)";
    $data = json_decode($input);
    $params = [
        'idCategory' => $data->idCategory,
        'strCategory' => $data->strCategory,
        'strCategoryThumb' => $data->strCategoryThumb,
        'strCategoryDescription' => $data->strCategoryDescription
    ];
    $stm = $conn->prepare($query);
    $stm->execute($params);
    echo json_encode(["success" => true]);

    

    }catch(Exception $e) {
    echo json_encode(["error" => $e->getMessage()]);
}

}




?> 