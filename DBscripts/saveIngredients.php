<?php

header("Content-Type: application/json");
$method = $_SERVER["REQUEST_METHOD"];
$input = file_get_contents("php://input");
$data  = json_decode($input);

if (!$data) {
    echo json_encode(["error" => "Invalid JSON"]);
    exit;
}

$host   = "localhost";
$dbname = "CUCINA";
$user   = "root";
$pass   = "";

if($method == "POST"){

    try{

    $conn = new PDO("mysql:host=$host;dbname=$dbname", $user, $pass);
    $query = "INSERT INTO ingredients
            (idIngredient, strIngredient, strDescription, strType)
        VALUES
            (:idIngredient, :strIngredient, :strDescription, :strType)
        ON DUPLICATE KEY UPDATE
            strIngredient  = VALUES(strIngredient),
            strDescription = VALUES(strDescription)
    ";

    $params = [
        ':idIngredient'  => (int) $data->idIngredient,
        ':strIngredient' => $data->strIngredient  ?? null,
        ':strDescription'=> $data->strDescription ?? null,
        ':strType'       => $data->strType        ?? null,
    ];
    $stm = $conn->prepare($query);
    $stm->execute($params);
    echo json_encode(["success" => true]);

    }catch(Exception $e) {
    echo json_encode(["error" => $e->getMessage()]);
}

}

?>
