// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title Bank Mirror Token ($BMIRROR)
 * @dev High-speed, low-fee utility token on Polygon for Bank Mirror Alert verification ecosystem.
 * Supports token payments for merchant alert top-ups with event emission for automated fulfillment.
 */

interface IERC20 {
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address recipient, uint256 amount) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transferFrom(address sender, address recipient, uint256 amount) external returns (bool);

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
}

contract BMIRRORToken is IERC20 {
    string public name = "Bank Mirror Token";
    string public symbol = "BMIRROR";
    uint8 public decimals = 18;
    uint256 private _totalSupply;
    address public owner;
    address public treasury;

    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;

    // Rate: 10 BMIRROR = 100 Alerts
    uint256 public constant TOKENS_PER_100_ALERTS = 10 * 10**18;

    // Events for Off-Chain Backend Worker Listeners
    event AlertsPurchased(
        string indexed merchantId,
        address indexed buyer,
        uint256 tokenAmount,
        uint256 alertsCredited,
        uint256 timestamp
    );
    event TreasuryUpdated(address indexed oldTreasury, address indexed newTreasury);

    modifier onlyOwner() {
        require(msg.sender == owner, "Only contract owner can execute");
        _;
    }

    constructor(address _treasury) {
        owner = msg.sender;
        treasury = _treasury != address(0) ? _treasury : msg.sender;
        // Initial mint: 10,000,000 $BMIRROR to Treasury
        _mint(treasury, 10_000_000 * 10**18);
    }

    function totalSupply() public view override returns (uint256) {
        return _totalSupply;
    }

    function balanceOf(address account) public view override returns (uint256) {
        return _balances[account];
    }

    function transfer(address recipient, uint256 amount) public override returns (bool) {
        _transfer(msg.sender, recipient, amount);
        return true;
    }

    function allowance(address holder, address spender) public view override returns (uint256) {
        return _allowances[holder][spender];
    }

    function approve(address spender, uint256 amount) public override returns (bool) {
        _approve(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address sender, address recipient, uint256 amount) public override returns (bool) {
        _transfer(sender, recipient, amount);
        uint256 currentAllowance = _allowances[sender][msg.sender];
        require(currentAllowance >= amount, "ERC20: transfer amount exceeds allowance");
        unchecked {
            _approve(sender, msg.sender, currentAllowance - amount);
        }
        return true;
    }

    /**
     * @notice Pay directly on-chain with $BMIRROR to buy alerts for a Bank Mirror merchant.
     * @param merchantId The 8-character Bank Mirror Merchant ID (e.g., 'B1D71377')
     * @param packageUnits Number of 100-alert units (e.g. 1 unit = 100 alerts for 10 BMIRROR)
     */
    function buyAlertsOnChain(string calldata merchantId, uint256 packageUnits) external {
        require(packageUnits > 0, "Must purchase at least 1 unit (100 alerts)");
        uint256 cost = packageUnits * TOKENS_PER_100_ALERTS;
        uint256 totalAlerts = packageUnits * 100;

        require(_balances[msg.sender] >= cost, "Insufficient $BMIRROR balance");

        // Send tokens directly to the master treasury
        _transfer(msg.sender, treasury, cost);

        // Emit event for the Python backend worker
        emit AlertsPurchased(merchantId, msg.sender, cost, totalAlerts, block.timestamp);
    }

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    function burn(uint256 amount) external {
        _burn(msg.sender, amount);
    }

    function setTreasury(address newTreasury) external onlyOwner {
        require(newTreasury != address(0), "Invalid address");
        emit TreasuryUpdated(treasury, newTreasury);
        treasury = newTreasury;
    }

    function _transfer(address sender, address recipient, uint256 amount) internal {
        require(sender != address(0), "ERC20: transfer from zero address");
        require(recipient != address(0), "ERC20: transfer to zero address");
        require(_balances[sender] >= amount, "ERC20: transfer amount exceeds balance");

        _balances[sender] -= amount;
        _balances[recipient] += amount;
        emit Transfer(sender, recipient, amount);
    }

    function _mint(address account, uint256 amount) internal {
        require(account != address(0), "ERC20: mint to zero address");
        _totalSupply += amount;
        _balances[account] += amount;
        emit Transfer(address(0), account, amount);
    }

    function _burn(address account, uint256 amount) internal {
        require(account != address(0), "ERC20: burn from zero address");
        require(_balances[account] >= amount, "ERC20: burn amount exceeds balance");
        _balances[account] -= amount;
        _totalSupply -= amount;
        emit Transfer(account, address(0), amount);
    }

    function _approve(address holder, address spender, uint256 amount) internal {
        require(holder != address(0), "ERC20: approve from zero address");
        require(spender != address(0), "ERC20: approve to zero address");
        _allowances[holder][spender] = amount;
        emit Approval(holder, spender, amount);
    }
}
