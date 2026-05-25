<?php


header("Content-Type: application/json");

$host   = "localhost";
$dbname = "CUCINA";
$user   = "root";
$pass   = "";

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    echo json_encode(["error" => "Solo POST e accettato."]);
    exit;
}

$input = file_get_contents("php://input");
$data  = json_decode($input);

if (!$data || !isset($data->idCategory) || !isset($data->strCategory)) {
    echo json_encode(["error" => "Payload non valido. Richiesti: idCategory, strCategory."]);
    exit;
}

// Sanitizzazione base: solo stringhe, nessun carattere di controllo
$idCategory  = intval($data->idCategory);
$strCategory = htmlspecialchars(strip_tags((string)$data->strCategory), ENT_QUOTES, 'UTF-8');

if ($idCategory <= 0 || empty($strCategory)) {
    echo json_encode(["error" => "Valori non validi."]);
    exit;
}

try {
    $conn = new PDO("mysql:host=$host;dbname=$dbname;charset=utf8mb4", $user, $pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION
    ]);

    // Controlla duplicato
    $check = $conn->prepare("SELECT idCategory FROM categories WHERE idCategory = ?");
    $check->execute([$idCategory]);
    if ($check->fetch()) {
        echo json_encode(["error" => "Categoria con idCategory=$idCategory gia presente."]);
        exit;
    }

    $stmt = $conn->prepare(
        "INSERT INTO categories (idCategory, strCategory) VALUES (:idCategory, :strCategory)"
    );
    $stmt->execute([
        ':idCategory'  => $idCategory,
        ':strCategory' => $strCategory,
    ]);

    echo json_encode(["success" => true, "idCategory" => $idCategory]);

} catch (PDOException $e) {
    // Non esporre dettagli interni in produzione
    error_log("save.php error: " . $e->getMessage());
    echo json_encode(["error" => "Errore del database."]);
}
?>
