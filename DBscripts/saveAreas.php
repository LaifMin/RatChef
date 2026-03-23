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
    $query = "INSERT INTO areas
            (strArea)
        VALUES
            (:strArea)
        ON DUPLICATE KEY UPDATE
            strArea = VALUES(strArea)
    ";

    $params = [
        ':strArea' => $data->strArea ?? null,
    ];
    $stm = $conn->prepare($query);
    $stm->execute($params);
    echo json_encode(["success" => true]);

    }catch(Exception $e) {
    echo json_encode(["error" => $e->getMessage()]);
}

}

?>
